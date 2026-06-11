# -*- coding: utf-8 -*-
"""
StockAI 本地主力嗅探系统 v3.3 Final
基于多表数据的横盘吸筹量化识别

╔══════════════════════════════════════════════════════════════════╗
║  v3.3 Final — 策略规范（2026-06-09 回测验证封版）                ║
╠══════════════════════════════════════════════════════════════════╣
║  【评分规则】总分范围: -5 ~ 40                                    ║
║    主力资金净流入 > 0（特大单+大单）    → +15  核心因子          ║
║    股东户数连续下降（2期或3期）        → +15  核心因子          ║
║    三日背离（连续3日主力流入+价格↓）  → +10  增强信号（可选）   ║
║    振幅异常（个股>30% 且 大盘<10%）   →  -5  条件性风险扣分     ║
║    融资低位：已移除（覆盖率不足）                                 ║
║                                                                  ║
║  【信号门槛】                                                     ║
║    ≥ 30分 → 强信号（双核心全满，目标阈值）                       ║
║    ≥ 15分 → 中信号（单核心满足）                                 ║
║    < 15分 → 过滤                                                 ║
║                                                                  ║
║  【止损规则】（2025全年回测验证：固定5%盈亏比1.79最优）           ║
║    主止损 = 买入价 × 0.95（固定5%）                              ║
║    结构止损 = 20日低点 × 0.98                                    ║
║    实际止损 = max(主止损, 结构止损) 取较紧值                      ║
║    最大单笔亏损上限: -5%，止损触发率: 38.4%                      ║
║                                                                  ║
║  【仓位规则】（2025全年回测验证：动态仓位最大回撤-34.5%最优）      ║
║    进攻模式 (attack)  → 强信号 15%，中信号 8%                    ║
║    防守模式 (defense) → 强信号  5%，中信号 2%                    ║
║    空仓模式 (empty)   → 不开新仓 0%                              ║
║    单股绝对上限: 20%（铁律）                                      ║
║                                                                  ║
║  【AI过滤】（本地Ollama，信心指数≥70才推送）                      ║
║    AI信心指数 ≥ 70 → 推送（附信心标注）                          ║
║    AI信心指数 < 70 → 跳过（日志记录）                            ║
║    AI离线（-1）    → 不过滤（保持兼容）                           ║
║                                                                  ║
║  【三重流出否决】主力+融资+北向均流出 → 总分清零                  ║
║  【微盘股过滤】流通市值 < 10亿 → 排除                            ║
╚══════════════════════════════════════════════════════════════════╝

【向后兼容】
  analyze_v2_1 / analyze_and_format 保持原有签名和行为。
  新方法: analyze_v3_0 / analyze_and_format_v3
"""

import io
import sys
import os
import json
import logging
import requests
import sqlite3
import pandas as pd
import tushare as ts
from datetime import datetime

# --- 路径 & 日志 ---
ROOT_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(ROOT_DIR, "db", "stock_daily.db")
OLLAMA_API = "http://localhost:11434/api/chat"

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Windows GBK 控制台不支持 emoji，强制 UTF-8
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# --- Tushare 初始化 ---
pro = None
try:
    from scripts.tokens import TOKEN as _TOKEN
    ts.set_token(_TOKEN)
    pro = ts.pro_api()
    log.info("Tushare init OK")
except Exception as _e:
    log.warning("Tushare init failed: %s", _e)


# =============================================================================
# StockAnalyzer v2.3 —— 多表数据，精确量化
# =============================================================================
class StockAnalyzer:
    """本地SQLite多表行情分析，v2.3 精确量化版"""

    def __init__(self, db_path: str = DB_PATH):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        log.info("DB connected: %s", db_path)

    # ── 数据读取（v2.3 增加 daily_basic / bak_basic） ──────────────────────
    def get_data_for_skill(self, ts_code: str, end_date: str = None) -> dict:
        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")

        # 日线行情（使用 daily_prices 表）
        df_daily = pd.read_sql(
            """SELECT ts_code, trade_date, open, high, low, close,
                      pre_close, change, pct_chg, vol, amount, adj_factor
               FROM   daily_prices
               WHERE  ts_code = ? AND trade_date <= ?
               ORDER  BY trade_date DESC LIMIT 120""",
            self.conn, params=(ts_code, end_date)
        )

        # 资金流向
        df_money = self._safe_read(
            """SELECT * FROM moneyflow
               WHERE ts_code = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 5""",
            (ts_code, end_date)
        )

        # 股东户数（长周期趋势）
        df_holder = self._safe_read(
            """SELECT * FROM stk_holdernumber
               WHERE ts_code = ?
               ORDER BY ann_date DESC LIMIT 3""",
            (ts_code,)
        )

        # 融资融券（120日分位）
        df_margin = self._safe_read(
            """SELECT * FROM margin_detail
               WHERE ts_code = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 120""",
            (ts_code, end_date)
        )

        # 大宗交易（60日）
        df_block = self._safe_read(
            """SELECT * FROM block_trade
               WHERE ts_code = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 60""",
            (ts_code, end_date)
        )

        # 分钟线（尾盘量）
        df_mins = self._safe_read(
            """SELECT * FROM stk_mins
               WHERE ts_code = ? AND trade_time <= ?
               ORDER BY trade_time DESC LIMIT 1200""",
            (ts_code, end_date + " 15:00:00")
        )

        # v2.3 增强：日频指标（换手率、股本、市值）
        df_daily_basic = self._safe_read(
            """SELECT * FROM daily_basic
               WHERE ts_code = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 120""",
            (ts_code, end_date)
        )

        # v2.3 增强：备用基础信息（含股东户数）
        df_bak = self._safe_read(
            """SELECT * FROM bak_basic
               WHERE ts_code = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 10""",
            (ts_code, end_date)
        )

        # v3.3 增强：北向资金数据（用于三重流出风险否决）
        # 取最近10日用于计算3日均值比较（消除单日噪声）
        df_hsgt = self._safe_read(
            """SELECT trade_date, north_money
               FROM hsgt_moneyflow
               WHERE trade_date <= ?
               ORDER BY trade_date DESC LIMIT 10""",
            (end_date,)
        )

        return {
            "daily": df_daily,
            "money": df_money,
            "holder": df_holder,
            "margin": df_margin,
            "block": df_block,
            "mins": df_mins,
            "daily_basic": df_daily_basic,
            "bak": df_bak,
            "hsgt": df_hsgt,
        }

    def _safe_read(self, sql, params):
        """表不存在时返回空DataFrame"""
        try:
            return pd.read_sql(sql, self.conn, params=params)
        except Exception:
            return pd.DataFrame()

    # ── 辅助计算 ──────────────────────────────────────────────────────────
    def _calc_tail_volume_ratio(self, mins_df, latest_date):
        """尾盘30分钟成交量占比"""
        if mins_df.empty:
            return -1
        mins_df = mins_df.copy()
        mins_df["trade_time"] = pd.to_datetime(mins_df["trade_time"])
        latest_day = pd.to_datetime(latest_date).date()
        df_day = mins_df[mins_df["trade_time"].dt.date == latest_day]
        if df_day.empty:
            return -1
        total_vol = df_day["vol"].sum()
        if total_vol == 0:
            return 0
        tail = df_day[
            (df_day["trade_time"].dt.hour == 14) & (df_day["trade_time"].dt.minute >= 30)
            | (df_day["trade_time"].dt.hour == 15)
        ]
        return tail["vol"].sum() / total_vol

    def _get_share_info(self, daily_basic_df):
        """从 daily_basic 获取最新股本信息"""
        if daily_basic_df.empty:
            return None, None, None, None
        row = daily_basic_df.iloc[0]
        float_share = row.get("float_share", None)      # 万股
        total_share = row.get("total_share", None)      # 万股
        circ_mv = row.get("circ_mv", None)              # 万元
        turnover = row.get("turnover_rate", None)       # %
        return float_share, total_share, circ_mv, turnover

    def _get_holder_num(self, bak_df, holder_df):
        """获取最新的股东户数：优先 bak_basic，其次 stk_holdernumber"""
        if not bak_df.empty:
            num = bak_df.iloc[0].get("holder_num", None)
            if num and num > 0:
                return int(num), "bak_basic"
        if not holder_df.empty:
            num = holder_df.iloc[0].get("holder_num", None)
            if num and num > 0:
                return int(num), "stk_holdernumber"
        return None, None

    # ── v3.0 新增因子 ──────────────────────────────────────────────────────

    def _check_consolidation_position(self, df: pd.DataFrame) -> tuple:
        """
        【因子1】横盘位置：股价距一年（约250日）最低点的涨幅。
        < 30%  → 位于底部区域，+5分
        >= 30% → 位于中高位，警惕派发，-5分
        返回 (score_delta: int, reason: str)
        """
        if len(df) < 20:
            return 0, ""
        # 取最多250日的低价
        low_250 = df.head(min(len(df), 250))["low"].min()
        latest_close = float(df.iloc[0]["close"])
        if pd.isna(low_250) or low_250 <= 0:
            return 0, "⛔ 无法计算横盘位置（低价数据异常）"
        rise_from_low = (latest_close / low_250) - 1
        if rise_from_low < 0.30:
            return 5, (f"✅ 横盘底部区域：距年低 {rise_from_low:.1%} < 30%，+5分")
        else:
            return -5, (f"⚠️ 横盘中高位：距年低 {rise_from_low:.1%} ≥ 30%，警惕派发，-5分")

    def _check_three_day_divergence(self, data: dict) -> tuple:
        """
        【因子2】三日资金背离：连续3日主力净流入，但股价累计涨跌幅 < 0%。
        这是最强的隐蔽吸筹信号之一。
        返回 (score_delta: int, reason: str)
        """
        df_m = data.get("money", pd.DataFrame())
        df_d = data.get("daily", pd.DataFrame())
        if df_m.empty or len(df_m) < 3 or df_d.empty or len(df_d) < 3:
            return 0, ""

        needed = {"buy_elg_amount", "sell_elg_amount",
                  "buy_lg_amount",  "sell_lg_amount"}
        if not needed.issubset(set(df_m.columns)):
            return 0, ""

        net_main_total = 0.0
        all_positive = True
        for i in range(3):
            row = df_m.iloc[i]
            net = (float(row["buy_elg_amount"] or 0) + float(row["buy_lg_amount"] or 0)
                   - float(row["sell_elg_amount"] or 0) - float(row["sell_lg_amount"] or 0))
            if net <= 0:
                all_positive = False
                break
            net_main_total += net

        if not all_positive:
            return 0, ""

        # 3日累计价格变化
        c0 = float(df_d.iloc[0]["close"])
        c2 = float(df_d.iloc[2]["close"])
        price_chg = (c0 / c2 - 1) if c2 > 0 else 0

        if price_chg < 0:
            return 10, (f"✅ 三日资金背离：主力连续3日净流入共 {net_main_total:.0f}万，"
                        f"但股价累计 {price_chg:.2%}，隐蔽吸筹信号，+10分")
        return 0, ""

    def _check_vol_convergence(self, df: pd.DataFrame) -> tuple:
        """
        【因子3】波动率收敛：近10日振幅 / 近20日振幅 < 0.7。
        振幅收窄意味着蓄势待发，变盘临近。
        返回 (score_delta: int, reason: str)
        """
        if len(df) < 20:
            return 0, ""
        df10 = df.head(10)
        df20 = df.head(20)
        low10, high10 = df10["low"].min(), df10["high"].max()
        low20, high20 = df20["low"].min(), df20["high"].max()
        if low10 <= 0 or low20 <= 0:
            return 0, ""
        amp_10 = (high10 / low10 - 1)
        amp_20 = (high20 / low20 - 1)
        if amp_20 <= 0:
            return 0, ""
        ratio = amp_10 / amp_20
        if ratio < 0.7:
            return 3, (f"✅ 波动率收敛：10日振幅/20日振幅 = {ratio:.2f} < 0.7，"
                       f"变盘临近，+3分")
        return 0, ""

    def _check_margin_trend(self, data: dict) -> tuple:
        """
        【因子4】融资情绪升级：连续3日融资余额下降，且股价3日累计涨幅 < 2%。
        融资盘出清 + 股价坚挺 = 主力接盘信号。
        返回 (score_delta: int, reason: str)
        """
        df_margin = data.get("margin", pd.DataFrame())
        df_d      = data.get("daily",  pd.DataFrame())
        if df_margin.empty or len(df_margin) < 3 or "rzye" not in df_margin.columns:
            return 0, ""
        rzye = df_margin["rzye"].dropna()
        if len(rzye) < 3:
            return 0, ""
        # 连续3日融资余额下降（最新在前）
        if not (rzye.iloc[0] < rzye.iloc[1] < rzye.iloc[2]):
            return 0, ""
        # 股价3日涨幅 < 2%
        if df_d.empty or len(df_d) < 3:
            return 0, ""
        c0 = float(df_d.iloc[0]["close"])
        c2 = float(df_d.iloc[2]["close"])
        price_chg = (c0 / c2 - 1) if c2 > 0 else 0
        chg_rzye  = rzye.iloc[0] - rzye.iloc[2]
        if price_chg < 0.02:
            return 3, (f"✅ 融资情绪升级：3日融资余额累计减少 {abs(chg_rzye):.0f}万，"
                       f"股价仅涨 {price_chg:.2%}，杠杆出清信号，+3分")
        return 0, ""

    # ── 核心评分 v2.3 ──────────────────────────────────────────────────────
    def analyze_v2_1(self, ts_code: str, catalyst_score: int = 0) -> tuple:
        data = self.get_data_for_skill(ts_code)
        df = data["daily"]

        if df.empty or len(df) < 2:
            return (
                {"volume_price": 0, "chip_structure": 0,
                 "market_behavior": 0, "catalyst": catalyst_score,
                 "risk_flag": False},
                ["❌ 数据不足（至少需要 2 日行情）"]
            )

        # ST检查
        name = ""
        try:
            row = self.conn.execute(
                "SELECT name FROM stock_list WHERE ts_code=?", (ts_code,)
            ).fetchone()
            if row:
                name = row[0]
        except Exception:
            pass
        if name and "ST" in name.upper():
            return (
                {"volume_price": 0, "chip_structure": 0,
                 "market_behavior": 0, "catalyst": catalyst_score,
                 "risk_flag": True},
                ["⛔ 触发风险否决：ST股"]
            )

        score_card = {
            "volume_price": 0,
            "chip_structure": 0,
            "market_behavior": 0,
            "catalyst": catalyst_score,
            "risk_flag": False
        }
        reasoning = []
        latest = df.iloc[0]
        yesterday = df.iloc[1] if len(df) > 1 else None

        # 增强数据
        float_share, total_share, circ_mv, turnover = self._get_share_info(data["daily_basic"])
        holder_num, holder_src = self._get_holder_num(data["bak"], data["holder"])

        # ── 第一层：量价异动 (25分) ──────────────────────────────────────
        recent_20 = df.head(20)
        amplitude = ((recent_20["high"].max() - recent_20["low"].min()) / recent_20["low"].min())
        if amplitude < 0.15:
            score_card["volume_price"] += 5
            reasoning.append(f"✅ 横盘形态 (20日振幅 {amplitude:.2%})")
        else:
            reasoning.append(f"❌ 振幅过大 ({amplitude:.2%})，非横盘")

        # 缩量：优先使用换手率
        if turnover is not None:
            recent_20_basic = data["daily_basic"].head(20)
            if len(recent_20_basic) >= 20:
                avg_turnover_20 = recent_20_basic["turnover_rate"].mean()
                basic_60 = data["daily_basic"].head(60)
                if len(basic_60) >= 60:
                    avg_turnover_60 = basic_60["turnover_rate"].mean()
                    if avg_turnover_60 > 0 and avg_turnover_20 < avg_turnover_60 * 0.5:
                        score_card["volume_price"] += 5
                        reasoning.append(f"✅ 缩量：近20日均换手率 {avg_turnover_20:.2f}% < 60日均值的50%")
                    else:
                        reasoning.append(f"⚠️ 未缩量 (20日均换手率 {avg_turnover_20:.2f}%)")
                else:
                    reasoning.append("⛔ 换手率数据不足60日")
            else:
                reasoning.append("⛔ 换手率数据不足20日")
        else:
            # 降级为成交量
            vol_20 = recent_20["vol"].mean()
            if len(df) >= 60:
                vol_60 = df.head(60)["vol"].mean()
                if vol_60 > 0 and vol_20 < vol_60 * 0.5:
                    score_card["volume_price"] += 5
                    reasoning.append("✅ 缩量（基于成交量）")
            reasoning.append("⛔ 无 daily_basic 换手率，使用成交量近似")

        # 隐蔽吸筹：大量小阳线
        if len(recent_20) >= 5:
            vol_5 = recent_20.head(5)["vol"].mean()
            if vol_5 > 0 and latest["vol"] > vol_5 * 2 and 0 < latest["pct_chg"] < 3:
                score_card["volume_price"] += 10
                reasoning.append("✅ 大量小阳线（隐蔽吸筹）")

        # 尾盘偷袭量
        tail_ratio = self._calc_tail_volume_ratio(data["mins"], latest["trade_date"])
        if tail_ratio >= 0:
            if tail_ratio > 0.35:
                score_card["volume_price"] += 5
                reasoning.append(f"✅ 尾盘偷袭量 ({tail_ratio:.2%})")
            else:
                reasoning.append(f"尾盘量占比 {tail_ratio:.2%}（正常）")
        else:
            reasoning.append("⛔ 无分钟线数据，无法计算尾盘量")

        # 大阴线次日确认
        if yesterday is not None and yesterday["pct_chg"] < -5:
            reasoning.append(f"⚠️ 昨日大阴线 (-{abs(yesterday['pct_chg']):.2f}%)，需观察今日形态确认，今日不做方向性判断")
            score_card["volume_price"] = min(score_card["volume_price"], 10)

        # ── 第二层：筹码分布 (25分) ──────────────────────────────────────
        chip_score = 0

        # 股东户数趋势（双源）
        if len(data["holder"]) >= 3:
            nums = data["holder"]["holder_num"].values
            if nums[-1] and nums[0] and nums[-1] != 0:
                if all(nums[i] <= nums[i+1] for i in range(len(nums)-1)):
                    total_chg = (nums[0] - nums[-1]) / nums[-1]
                    if total_chg < -0.10:
                        chip_score += 15
                        reasoning.append(f"✅ 筹码高度集中（stk_holdernumber）：累计 {total_chg:.2%}")
                    elif total_chg < -0.05:
                        chip_score += 10
                        reasoning.append(f"🔶 筹码集中：下降 {total_chg:.2%}")
                    else:
                        chip_score += 5
                        reasoning.append(f"股东户数微降 {total_chg:.2%}")
                else:
                    reasoning.append("❌ 股东户数未连续下降")
        elif holder_num is not None and len(data["holder"]) >= 1:
            prev_holder = data["holder"].iloc[-1]["holder_num"]
            if prev_holder and prev_holder != 0:
                chg = (holder_num - prev_holder) / prev_holder
                if chg < -0.05:
                    chip_score += 8
                    reasoning.append(f"🔶 筹码集中（{holder_src} vs stk_holdernumber）：减少 {chg:.2%}")
                else:
                    reasoning.append(f"股东户数变化不明显 ({chg:.2%})")
        elif holder_num is not None:
            reasoning.append(f"股东户数 {holder_num} 户（来源：{holder_src}），缺少历史对比")
        else:
            reasoning.append("⛔ 股东户数数据缺失")

        # 融资余额分位
        if not data["margin"].empty:
            margin_series = data["margin"]["rzye"].dropna()
            if len(margin_series) >= 10:
                latest_rzye = margin_series.iloc[0]
                pct_rank = (margin_series <= latest_rzye).mean()
                if circ_mv and circ_mv > 0:
                    fin_ratio = (latest_rzye * 10000) / circ_mv  # 元转万元
                    if pct_rank > 0.9 and fin_ratio > 0.05:
                        reasoning.append(f"⚠️ 融资踩踏风险：分位 {pct_rank:.0%}，占流通市值 {fin_ratio:.2%}")
                        chip_score -= 5
                    elif pct_rank < 0.3:
                        chip_score += 5
                        reasoning.append(f"✅ 融资余额低位（分位 {pct_rank:.0%}），杠杆出清")
                else:
                    reasoning.append("⛔ 无流通市值数据，无法评估融资占比")
        else:
            reasoning.append("⛔ 融资融券数据缺失")

        # 大宗交易溢价
        if not data["block"].empty and not data["daily"].empty:
            if "premium" in data["block"].columns:
                avg_premium = data["block"]["premium"].mean()
                if avg_premium > 0:
                    chip_score += 3
                    reasoning.append(f"✅ 近期大宗交易平均溢价 {avg_premium:.2%}")
            else:
                block_df = data["block"].merge(
                    data["daily"][["trade_date", "close"]], on="trade_date", how="left"
                )
                if "price" in block_df.columns:
                    block_df["calc_premium"] = (block_df["price"] / block_df["close"] - 1)
                    avg_prem = block_df["calc_premium"].mean()
                    if avg_prem > 0:
                        chip_score += 3
                        reasoning.append(f"✅ 大宗交易平均溢价（计算） {avg_prem:.2%}")
                    else:
                        reasoning.append("大宗交易平均溢价为负或零")
                else:
                    reasoning.append("大宗交易表缺少价格字段")
        else:
            reasoning.append("⛔ 大宗交易数据缺失")

        chip_score = max(-5, min(25, chip_score))
        score_card["chip_structure"] = chip_score

        # ── 第三层：盘口行为 (20分) ──────────────────────────────────────
        if not data["money"].empty:
            m = data["money"].iloc[0]
            needed = {"buy_elg_amount", "buy_lg_amount", "sell_elg_amount", "sell_lg_amount"}
            if needed.issubset(set(m.index)):
                net_big = (m["buy_elg_amount"] + m["buy_lg_amount"]
                           - m["sell_elg_amount"] - m["sell_lg_amount"])
                net_elg = m["buy_elg_amount"] - m["sell_elg_amount"]
                net_lg  = m["buy_lg_amount"] - m["sell_lg_amount"]

                if net_big > 0 and latest["pct_chg"] < 0:
                    score_card["market_behavior"] += 15
                    reasoning.append(f"✅ 正向背离：跌 {latest['pct_chg']:.2f}%，主力净流入 {net_big:.0f} 万元")
                elif net_big > 0:
                    score_card["market_behavior"] += 8
                    reasoning.append(f"🔶 主力净流入 {net_big:.0f} 万元，股价同向")
                elif net_big < 0 and latest["pct_chg"] > 0:
                    reasoning.append(f"⚠️ 反向警示：股价涨但主力净流出 {abs(net_big):.0f} 万元")

                if net_elg > 0 and net_lg < 0:
                    reasoning.append("🔍 特大单买入、大单卖出，机构承接+短线兑现")
                elif net_elg > 0 and net_lg > 0:
                    reasoning.append("🔍 特大单与大单同步流入，主力锁仓")
            else:
                reasoning.append("⚠️ 资金流字段不完整")
        else:
            reasoning.append("⛔ 无资金流向数据，盘口评分为0")

        # ── 第五层：风险否决 ───────────────────────────────────────────
        # 跌停否决
        if latest["pct_chg"] < -9.5:
            score_card["risk_flag"] = True
            reasoning.append("⛔ 触发风险否决：今日跌停")

        # 主力+融资双流出
        if not data["money"].empty and not data["margin"].empty:
            m = data["money"].iloc[0]
            if needed.issubset(set(m.index)):
                net_main = (m["buy_elg_amount"] + m["buy_lg_amount"]
                            - m["sell_elg_amount"] - m["sell_lg_amount"])
                if len(data["margin"]) >= 2:
                    margin_chg = data["margin"].iloc[0]["rzye"] - data["margin"].iloc[1]["rzye"]
                else:
                    margin_chg = 0
                if net_main < 0 and margin_chg < 0:
                    reasoning.append("⚠️ 主力+融资双流出（北向缺失），市场情绪偏空，扣5分")
                    score_card["market_behavior"] = max(0, score_card["market_behavior"] - 5)

        return score_card, reasoning

    # ── v3.3 核心评分（双核心回归 + 振幅条件性负面过滤）────────────────────
    def analyze_v3_0(self, ts_code: str,
                     catalyst_score: int = 0,
                     industry_mode: str = "normal") -> tuple:
        """
        StockAnalyzer v3.3 评分方法（双核心回归）。

        策略基线 (v3.3, 回归验证日期 2026-06-09):
          - 强信号阈值: 总分 >= 30（双核心全满）
          - 历史表现基线 (2024H1):
              - 信号占比: 42,914 / 647,796 ≈ 6.6%
              - 10日胜率: 60.7%
              - 10日均收益: +4.12%
          - 核心因子: 主力资金净流入(+15), 股东户数连续下降(+15)
          - 增强因子: 三日背离(+10，辅助增强信号)
          - 振幅过滤: 个股振幅>30% 且 大盘振幅<10% → -5分（条件性风险警示）
          - 融资低位: 已移除（数据覆盖不全，边际贡献不显著）

        评分规则（总分范围：-5 ~ 40）：
          主力资金净流入 > 0（特大单+大单净买入）  +15  核心因子
          股东户数连续下降（近2期或3期）          +15  核心因子
          连续3日主力净流入 + 股价累计涨幅 < 0%    +10  增强信号（三日背离）
          个股振幅>30% 且 大盘振幅<10%              -5   条件性风险警示

        门槛：
          ≥30（双核心全满）→ 强信号
          ≥15（单核心满足）→ 中信号
          <15 → 过滤

        参数：
            ts_code        股票代码
            catalyst_score 外部传入的催化层得分（保留参数，兼容旧接口）
            industry_mode  行业层级（"main"/"backup"/"avoid"），avoid 触发一票否决

        返回：
            (score_card: dict, reasoning: list)
        """
        # 行业层级一票否决
        if industry_mode == "avoid":
            return (
                {"volume_price": 0, "chip_structure": 0,
                 "market_behavior": 0, "catalyst": catalyst_score,
                 "risk_flag": True, "total_score": 0},
                [f"⛔ 行业一票否决：当前行业不在主线/备选列表（industry_mode=avoid）"]
            )

        data = self.get_data_for_skill(ts_code)
        df = data["daily"]

        if df.empty or len(df) < 2:
            return (
                {"volume_price": 0, "chip_structure": 0,
                 "market_behavior": 0, "catalyst": catalyst_score,
                 "risk_flag": False, "total_score": 0},
                ["❌ 数据不足（至少需要 2 日行情）"]
            )

        # ST检查
        name = ""
        try:
            row = self.conn.execute(
                "SELECT name FROM stock_list WHERE ts_code=?", (ts_code,)
            ).fetchone()
            if row:
                name = row[0]
        except Exception:
            pass
        if name and "ST" in name.upper():
            return (
                {"volume_price": 0, "chip_structure": 0,
                 "market_behavior": 0, "catalyst": catalyst_score,
                 "risk_flag": True, "total_score": 0},
                ["⛔ 触发风险否决：ST股"]
            )

        score_card = {
            "volume_price": 0,
            "chip_structure": 0,
            "market_behavior": 0,
            "catalyst": catalyst_score,
            "risk_flag": False,
            "total_score": 0
        }
        reasoning = []
        total_score = 0

        # ========== 主力拆单预设与判断 ==========
        is_split_order = False
        main_net_3day = "非连续流入"
        small_net_ratio = 0.0
        holder_trend = "未下降"
        vol_trend = "其他"
        amplitude_val = 999.0
        
        # 1. 资金指标
        if not data["money"].empty:
            m = data["money"].iloc[0]
            total_sm_inflow = float(m.get("buy_sm_amount", 0) - m.get("sell_sm_amount", 0))
            total_mf_amount = sum(float(m.get(c, 0)) for c in ["buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount", "sell_sm_amount", "sell_md_amount", "sell_lg_amount", "sell_elg_amount"])
            small_net_ratio = (total_sm_inflow / total_mf_amount * 100) if total_mf_amount > 0 else 0.0
            
            if len(data["money"]) >= 3:
                inflows = []
                for i in range(3):
                    m_i = data["money"].iloc[i]
                    net_big_i = (float(m_i.get("buy_elg_amount", 0)) + float(m_i.get("buy_lg_amount", 0))
                                 - float(m_i.get("sell_elg_amount", 0)) - float(m_i.get("sell_lg_amount", 0)))
                    inflows.append(net_big_i > 0)
                if all(inflows):
                    main_net_3day = "连续流入"

        # 2. 筹码指标
        if len(data["holder"]) >= 2:
            nums = data["holder"]["holder_num"].dropna().values
            if len(nums) >= 2 and nums[0] <= nums[1]:
                holder_trend = "连续2~3期下降"

        # 3. 量能与振幅指标
        if len(df) >= 20:
            recent_20 = df.head(20)
            amplitude_val = ((recent_20["high"].max() - recent_20["low"].min()) / recent_20["low"].min()) * 100
            
            vol_5 = recent_20.head(5)["vol"].mean()
            vol_20 = recent_20["vol"].mean()
            if vol_20 > 0:
                if vol_5 < vol_20 * 0.7:
                    close_3d = df.head(3)["close"]
                    if close_3d.min() > 0 and (close_3d.max() / close_3d.min() - 1) < 0.03:
                        vol_trend = "缩量企稳"
                elif vol_20 * 1.2 < vol_5 < vol_20 * 2.0:
                    vol_trend = "温和放量"

        # 组合条件判定主力拆单
        cond1 = (main_net_3day != "连续流入") and (small_net_ratio > 60)
        cond2 = (holder_trend == "连续2~3期下降")
        cond3 = (vol_trend in ["温和放量", "缩量企稳"])
        cond4 = (amplitude_val <= 30)
        
        match_count = sum([cond1, cond2, cond3, cond4])
        if match_count >= 2:
            is_split_order = True
            reasoning.append("🔍 触发主力拆单吸筹判定")

        # ── 因子1：主力资金净流入及主力拆单修正 ───────────
        if is_split_order:
            total_score += 20
            reasoning.append("✅ 主力拆单吸筹修正：强制按【连续3日流入】计分，+20分")
            score_card["market_behavior"] += 20
        elif not data["money"].empty:
            m = data["money"].iloc[0]
            needed = {"buy_elg_amount", "buy_lg_amount", "sell_elg_amount", "sell_lg_amount"}
            if needed.issubset(set(m.index)):
                net_big = (float(m["buy_elg_amount"] or 0) + float(m["buy_lg_amount"] or 0)
                           - float(m["sell_elg_amount"] or 0) - float(m["sell_lg_amount"] or 0))
                if net_big > 0:
                    total_score += 15
                    reasoning.append(f"✅ 主力净流入 {net_big:.0f}万，+15分")
                    score_card["market_behavior"] += 15
                else:
                    reasoning.append(f"❌ 主力净流出 {abs(net_big):.0f}万，未达加分条件")

        # 1. 小单占比扣分豁免
        if is_split_order and small_net_ratio > 60:
            reasoning.append("✅ 主力拆单豁免：取消小单占比扣分")

        # ── 因子2：股东户数连续下降（近2期或3期）→ +15分 ──────────────────
        holder_num, holder_src = self._get_holder_num(data["bak"], data["holder"])
        chip_score = 0
        if len(data["holder"]) >= 2:
            nums = data["holder"]["holder_num"].values
            if len(nums) >= 3:
                if nums[0] and nums[1] and nums[2] and nums[2] != 0:
                    if nums[0] <= nums[1] <= nums[2]:
                        total_chg = (nums[0] - nums[2]) / nums[2]
                        total_score += 15
                        reasoning.append(f"✅ 筹码集中（3期连续下降）：{total_chg:.2%}，+15分")
                        chip_score += 15
                    else:
                        reasoning.append("❌ 股东户数未连续下降（3期）")
            elif len(nums) >= 2:
                if nums[0] and nums[1] and nums[1] != 0:
                    if nums[0] <= nums[1]:
                        total_chg = (nums[0] - nums[1]) / nums[1]
                        total_score += 15
                        reasoning.append(f"✅ 筹码集中（2期连续下降）：{total_chg:.2%}，+15分")
                        chip_score += 15
                    else:
                        reasoning.append("❌ 股东户数未连续下降（2期）")
        else:
            reasoning.append("⚠️ 股东户数数据不足¼期")
        score_card["chip_structure"] = chip_score

        # ── 因子3：连续3日主力净流入 + 股价累计涨幅 < 0% → +10分（三日背离·增强信号）──
        delta, reason = self._check_three_day_divergence(data)
        if delta > 0 and reason:
            total_score += 10
            reasoning.append(
                "[增强信号] " + reason.replace("+5分", "+10分").replace("+10分", "+10分")
            )

        # ── 融资低位因子已移除 ────────────────────────────────────────────────
        # 原因：数据覆盖不全（margin_detail 与回测周期不对齐）且边际贡献不显著
        # 三重流出否决中仍保留融资方向判断（作为否决条件，非加分条件）

        # ── 振幅条件性风险警示 → -5分 ────────────────────────────────────────
        # 条件：个股20日振幅 > 30% 且 沪深300振幅 < 10%
        # 语义：市场平静期个股波动异常放大，排查爆炒/操控
        AMP_VETO_THRESHOLD    = 0.30   # 个股振幅阈值（可调）
        INDEX_STABLE_THRESHOLD = 0.10  # 大盘振幅闰值（可调）

        if len(df) >= 20:
            recent_20 = df.head(20)
            amplitude = ((recent_20["high"].max() - recent_20["low"].min()) / recent_20["low"].min())

            if amplitude > AMP_VETO_THRESHOLD:
                index_amplitude = None
                try:
                    df_idx = pd.read_sql(
                        """SELECT high, low FROM daily_index
                           WHERE ts_code='000300.SH'
                           ORDER BY trade_date DESC LIMIT 20""",
                        self.conn
                    )
                    if len(df_idx) >= 20:
                        idx_h = df_idx["high"].max()
                        idx_l = df_idx["low"].min()
                        if idx_l and idx_l > 0:
                            index_amplitude = (idx_h - idx_l) / idx_l
                except Exception:
                    pass

                if index_amplitude is not None and index_amplitude < INDEX_STABLE_THRESHOLD:
                    total_score -= 5
                    score_card["volume_price"] -= 5
                    reasoning.append(
                        f"⚠️ 振幅风险警示：个股振幅{amplitude:.2%}（>{AMP_VETO_THRESHOLD:.0%}）"
                        f"且大盘振幅仅{index_amplitude:.2%}（<{INDEX_STABLE_THRESHOLD:.0%}），"
                        f"个股波动异常放大，-5分"
                    )
                elif index_amplitude is None:
                    reasoning.append(f"⚠️ 个股振幅{amplitude:.2%}（>{AMP_VETO_THRESHOLD:.0%}），大盘数据缺失，无法判断异常")
                else:
                    reasoning.append(f"⚠️ 个股振幅{amplitude:.2%}，大盘振幅{index_amplitude:.2%}（内市整体波动，不扣分）")
            else:
                reasoning.append(f"✅ 个股振幅{amplitude:.2%} ≤ {AMP_VETO_THRESHOLD:.0%}，波动正常")

        # ── 三重流出风险否决 ──────────────────────────────────────────────────
        money_out = False
        margin_down = False
        hsgt_out = False

        if not data["money"].empty:
            m = data["money"].iloc[0]
            needed = {"buy_elg_amount", "buy_lg_amount", "sell_elg_amount", "sell_lg_amount"}
            if needed.issubset(set(m.index)):
                net_main = (float(m["buy_elg_amount"] or 0) + float(m["buy_lg_amount"] or 0)
                           - float(m["sell_elg_amount"] or 0) - float(m["sell_lg_amount"] or 0))
                if net_main < 0:
                    money_out = True

        if len(data["margin"]) >= 2:
            margin_chg = data["margin"].iloc[0]["rzye"] - data["margin"].iloc[1]["rzye"]
            if margin_chg < 0:
                margin_down = True

        if len(data["hsgt"]) >= 6:
            try:
                # P1 校准：用3日均值 vs 前3日均值，消除单日噪声
                # iloc[0]:最新, iloc[1]:前1日, ..., iloc[5]:前5日
                nm = data["hsgt"]["north_money"].apply(
                    lambda x: float(x) if x is not None else float("nan")
                )
                recent_3d_avg = nm.iloc[:3].mean()   # 近3日均值
                prev_3d_avg   = nm.iloc[3:6].mean()  # 前3日均值
                if not (pd.isna(recent_3d_avg) or pd.isna(prev_3d_avg)):
                    if recent_3d_avg < prev_3d_avg:   # 近3日均值 < 前3日均值 = 北向流出趋势
                        hsgt_out = True
            except (ValueError, TypeError):
                pass
        elif len(data["hsgt"]) >= 2:
            # 数据不足6条时，降级为日环比
            try:
                hsgt_chg = float(data["hsgt"].iloc[0]["north_money"]) - float(data["hsgt"].iloc[1]["north_money"])
                if hsgt_chg < 0:
                    hsgt_out = True
            except (ValueError, TypeError):
                pass

        if money_out and margin_down and hsgt_out:
            score_card["risk_flag"] = True
            reasoning.append("⛔ 触发三重流出否决：主力+融资+北向资金均流出")
            total_score = 0

        score_card["total_score"] = total_score
        return score_card, reasoning

    # ── 分数解读 & 报告生成 ──────────────────────────────────────────────
    def score_to_text_v2_1(self, score_card: dict) -> str:
        if score_card["risk_flag"]:
            return "⚠️ **存在重大风险，不建议参与。**\n"

        sv, sc, sm, sca = score_card["volume_price"], score_card["chip_structure"], \
                          score_card["market_behavior"], score_card["catalyst"]
        total = sv + sc + sm + sca

        t1 = "### 🔍 情绪与筹码扫描\n"
        if sv >= 20:
            t1 += "- **量价结构优秀**：高度控盘，呈现'底部横盘吸筹'特征。"
        elif sv >= 10:
            t1 += "- **量价结构尚可**：存在底部特征，吸筹力度待加强。"
        else:
            t1 += "- **量价结构较弱**：市场活跃度低，需等待底部信号。"

        if sc >= 15:
            t1 += " 且 **筹码高度集中**，抛压极轻。\n"
        elif sc >= 8:
            t1 += " 且 **筹码有集中迹象**，资金正在悄悄收集。\n"
        else:
            t1 += " 且 **筹码较分散**，市场成本不统一，需谨慎。\n"

        t2 = "### ⚡ 资金流向监控\n"
        if sm >= 20:
            t2 += "- **主力资金强势流入**：逆势吸筹迹象明显，资金极看好。\n"
        elif sm >= 10:
            t2 += "- **主力资金温和流入**：资金面支持良好，有积极动能。\n"
        else:
            t2 += "- **主力资金无明显动向**：需关注后续资金是否介入。\n"

        t3 = "### 🎯 外部催化因素\n"
        if sca >= 20:
            t3 += "- **行业趋势明确**：高景气周期，政策或技术突破驱动。\n"
        elif sca >= 10:
            t3 += "- **行业存在潜在催化**：市场有期待，待验证。\n"
        else:
            t3 += "- **行业发展平淡**：依赖技术面修复。\n"

        t4 = "\n### 📊 综合评级与建议\n"
        if total >= 60:
            t4 += "**评级：极具潜力 (Strong Buy)**\n"
            t4 += "1. **深度介入**：技术面与资金面均处理想状态，是难得的介入时机。\n"
            t4 += "2. **逻辑支撑**：横盘吸筹 + 筹码集中 + 主力流入，三位一体验证底部确权。\n"
            t4 += "3. **仓位管理**：可适当加仓，设好止损位，等待趋势确认。\n"
        elif total >= 40:
            t4 += "**评级：机会较大 (Buy)**\n"
            t4 += "1. **逐步建仓**：小仓位试探，耐心等待信号强化。\n"
            t4 += "2. **关注催化**：重点跟踪行业动态，等待外部利好确认。\n"
        elif total >= 20:
            t4 += "**评级：观望等待 (Hold)**\n"
            t4 += "1. **保持观望**：信号不明确，多看少动。\n"
            t4 += "2. **设定条件**：等待有效突破或资金显著流入后再决策。\n"
        else:
            t4 += "**评级：风险较高 (Avoid)**\n"
            t4 += "1. **规避风险**：市场环境不佳，暂不参与。\n"
            t4 += "2. **等待反转**：等待企稳并出现明确底部信号后再考虑。\n"

        return t1 + t2 + t3 + t4

    def score_to_text_v3_1(self, score_card: dict) -> str:
        """v3.2 分数解读（振幅正向因子 + 权重再平衡）"""
        if score_card["risk_flag"]:
            return "⚠️ **存在重大风险，不建议参与。**\n"

        total = score_card.get("total_score", 0)

        t1 = "### 📊 v3.2 评分结果\n"
        if total >= 30:
            t1 += "**评级：强信号 (Strong Signal)**\n"
            t1 += "- 振幅收敛+资金+筹码三重共振，主力吸筹迹象明显。\n"
            t1 += "- 建议重点关注，可积极布局。\n"
        elif total >= 20:
            t1 += "**评级：中信号 (Medium Signal)**\n"
            t1 += "- 部分核心因子满足，存在一定机会。\n"
            t1 += "- 建议谨慎参与，设置严格止损。\n"
        else:
            t1 += "**评级：过滤 (Filtered)**\n"
            t1 += "- 信号强度不足，建议继续观察。\n"
            t1 += "- 等待振幅收敛、资金和筹码条件同时满足。\n"

        return t1

    def generate_risk_report(self, ts_code: str, reasoning: list) -> str:
        r = "# ⛔ 风险警示报告\n\n**标的**：{}\n".format(ts_code)
        r += "**时间**：{}\n\n".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        r += "### 否决原因\n"
        for line in reasoning:
            r += "- {}\n".format(line)
        r += "\n**结论**：触发风险否决，**强烈建议观望**，不参与任何方向操作。\n"
        return r

    def analyze_and_format(self, ts_code: str, catalyst_score: int = 0) -> str:
        score_card, reasoning = self.analyze_v2_1(ts_code, catalyst_score)
        data = self.get_data_for_skill(ts_code)

        if score_card["risk_flag"]:
            return self.generate_risk_report(ts_code, reasoning)

        # 基础信息
        name = industry = area = list_date = "未知"
        try:
            row = pd.read_sql(
                "SELECT name, industry, area, list_date FROM stock_list WHERE ts_code=?",
                self.conn, params=(ts_code,)
            )
            if not row.empty:
                name = row.iloc[0].get("name", "未知")
                industry = row.iloc[0].get("industry", "未知")
                area = row.iloc[0].get("area", "未知")
                list_date = row.iloc[0].get("list_date", "未知")
        except Exception as e:
            log.warning("获取基础信息失败 %s: %s", ts_code, e)

        rpt  = "# 📈 AI量化股票诊断报告（v2.3）\n\n"
        rpt += "**分析标的**：{}（{}）\n".format(ts_code, name)
        rpt += "**分析时间**：{}\n\n".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        rpt += "### 🏢 股票基本信息\n"
        rpt += "- **代码**：{} | **名称**：{}\n".format(ts_code, name)
        rpt += "- **行业**：{} | **地区**：{} | **上市**：{}\n\n".format(industry, area, list_date)

        rpt += self.score_to_text_v2_1(score_card)

        # 风险控制
        rpt += "\n### 🛡️ 智能风险控制\n"
        if score_card["volume_price"] < 10:
            rpt += "1. **横盘确认不足**：建议等待更多交易日确认底部形态。\n"
        if score_card["chip_structure"] < 8:
            rpt += "2. **筹码集中度有待提升**：密切关注股东户数变化。\n"
        if score_card["market_behavior"] < 10:
            rpt += "3. **主力资金需加强**：等待更明确的资金流入信号。\n"

        # 止损参考
        if not data["daily"].empty:
            latest = data["daily"].iloc[0]
            low_20 = data["daily"].head(20)["low"].min()
            stop = low_20 * 0.98 if latest["pct_chg"] > 0 else latest["open"] * 0.99
            rpt += "\n**止损参考**：当前价 {:.2f}，建议止损位 {:.2f}。\n".format(latest["close"], stop)

        total = sum([score_card["volume_price"], score_card["chip_structure"],
                     score_card["market_behavior"], score_card["catalyst"]])
        rpt += "\n### 🎯 智能决策辅助\n"
        if total >= 60:
            rpt += "✅ **极具潜力**：建议深度介入，大胆建仓。\n"
        elif total >= 40:
            rpt += "⚠️ **机会较大**：建议逐步建仓，密切关注催化因素。\n"
        elif total >= 20:
            rpt += "🤔 **观望等待**：等待有效突破后再做决定。\n"
        else:
            rpt += "❌ **风险较高**：等待企稳并出现明确底部信号后再考虑。\n"

        rpt += "\n### 🧠 AI深度解读\n"
        rpt += "**核心逻辑**：\n"
        rpt += "1. **情绪共鸣**：量价表现显示该股正处于底部吸筹关键阶段。\n"
        rpt += "2. **资金博弈**：主力大单与散户换手率正进行激烈博弈。\n"
        trend = "上升" if score_card["catalyst"] >= 15 else ("平稳" if score_card["catalyst"] >= 5 else "低迷")
        signal = "利好" if score_card["catalyst"] >= 15 else ("中性" if score_card["catalyst"] >= 5 else "压力")
        rpt += "3. **行业趋势**：{} 行业处于 **{}** 阶段，存在 **{}** 因素。\n".format(industry, trend, signal)

        rpt += "\n### 📋 量化线索明细\n"
        for line in reasoning:
            rpt += "- {}\n".format(line)

        return rpt

    def analyze_and_format_v3(
        self,
        ts_code:        str,
        catalyst_score: int = 0,
        industry_mode:  str = "normal",
        market_mode:    str = "defense",
        max_pos:        float = 0.30,
    ) -> tuple:
        """
        v3.0 版完整报告方法。
        在 analyze_and_format 基础上额外返回结构化交易计划 JSON。

        返回：
            (report_str: str, trade_plan_dict: dict)
        """
        from trade_plan import generate_trade_plan, format_plan_for_feishu

        score_card, reasoning = self.analyze_v3_0(
            ts_code, catalyst_score, industry_mode
        )
        data = self.get_data_for_skill(ts_code)

        if score_card.get("risk_flag"):
            return self.generate_risk_report(ts_code, reasoning), {}

        # 基础信息
        name = industry = area = list_date = "未知"
        try:
            row = pd.read_sql(
                "SELECT name, industry, area, list_date FROM stock_list WHERE ts_code=?",
                self.conn, params=(ts_code,)
            )
            if not row.empty:
                name      = row.iloc[0].get("name",      "未知")
                industry  = row.iloc[0].get("industry",  "未知")
                area      = row.iloc[0].get("area",      "未知")
                list_date = row.iloc[0].get("list_date", "未知")
        except Exception as e:
            log.warning("获取基础信息失败 %s: %s", ts_code, e)

        total = score_card.get("total_score", 0)

        rpt  = f"# 📈 AI量化股票诊断报告（v3.1）\n\n"
        rpt += f"**分析标的**：{ts_code}（{name}）\n"
        rpt += f"**分析时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        rpt += f"**大盘模式**：{'进攻' if market_mode=='attack' else '防守' if market_mode=='defense' else '空仓'}\n"
        rpt += f"**行业层级**：{'主线' if industry_mode=='main' else '备选' if industry_mode=='backup' else '普通'}\n\n"

        rpt += "### 🏢 股票基本信息\n"
        rpt += f"- **代码**：{ts_code} | **名称**：{name}\n"
        rpt += f"- **行业**：{industry} | **地区**：{area} | **上市**：{list_date}\n\n"

        rpt += self.score_to_text_v3_1(score_card)

        # v3.2 得分汇总（振幅正向因子 + 权重再平衡）
        rpt += "\n### 📊 v3.2 评分汇总（总分范围：0 ~ 35）\n"
        rpt += f"| 因子 | 得分 | 规则说明 |\n"
        rpt += f"|:---|:---:|:---|\n"
        rpt += f"| 振幅收敛（IC最高）| {score_card['volume_price']} | <15%→+8 | 15~25%→+5 | 25~30%→+2 | >30%→0 |\n"
        rpt += f"| 主力资金 | {score_card['market_behavior']} | 净流入+12分（核心因子）|\n"
        rpt += f"| 筹码结构 | {score_card['chip_structure']} | 连续下降+15分（核心因子）|\n"
        rpt += f"| 三日背离 | 单独计算 | 连续3日净流入且股价未涨+5分 |\n"
        rpt += f"| 融资低位 | 单独计算 | 分位<30%+3分 |\n"
        rpt += f"| **综合得分** | **{total}** | **阈值：≥30强信号 / ≥20中信号** |\n\n"

        rpt += "### 📋 量化线索明细\n"
        for line in reasoning:
            rpt += f"- {line}\n"

        # 生成交易计划
        price_ctx = None
        if not data["daily"].empty:
            df = data["daily"]
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df["low"]   = pd.to_numeric(df["low"],   errors="coerce")
            price_ctx = {
                "close":   float(df.iloc[0]["close"]),
                "low_20":  float(df.head(20)["low"].min()),
                "low_60":  float(df.head(60)["low"].min()) if len(df) >= 60 else float(df["low"].min()),
                "ma5":     float(df.head(5)["close"].mean()),
                "ma20":    float(df.head(20)["close"].mean()),
                "pct_chg": float(df.iloc[0]["pct_chg"]) if pd.notna(df.iloc[0]["pct_chg"]) else 0.0,
            }

        plan = generate_trade_plan(
            ts_code    = ts_code,
            score      = float(total),
            score_card = score_card,
            market_mode= market_mode,
            max_pos    = max_pos,
            price_ctx  = price_ctx,
            conn       = self.conn,
        )

        rpt += format_plan_for_feishu(plan, name=name)

        # v3.2 AI增强：添加个股近期动态上下文
        try:
            from ai_context import build_ai_context
            ai_ctx = build_ai_context(ts_code, industry, name, self.conn)
            if ai_ctx:
                rpt += "\n### 🧠 AI深度分析\n"
                rpt += "**个股近期动态**：\n"
                rpt += ai_ctx.replace("【个股近期动态】", "") + "\n"
                
                # 调用Ollama进行深度分析，获取信心指数
                prompt = build_ollama_prompt(ts_code, score_card, reasoning, ai_ctx)
                ai_analysis, ai_confidence = call_ollama(prompt)
                if ai_analysis:
                    rpt += "\n**AI解读**：\n"
                    rpt += ai_analysis + "\n"
                # 将信心指数保存到 score_card，供下游推送过滤使用
                score_card["ai_confidence"] = ai_confidence
        except Exception as e:
            log.warning("AI分析模块调用失败: %s", e)
            rpt += "\n### 🧠 AI深度分析\n"
            rpt += "⚠️ AI分析模块暂不可用\n"

        return rpt, plan

    def close(self):
        self.conn.close()
        log.info("Database connection closed")


# --- Ollama 接口（v3.3 AI增强版 + 信心指数）---
def build_ollama_prompt(ts_code: str, score_card: dict, reasoning: list,
                        ai_context: str = "") -> str:
    total = score_card.get("total_score",
                           score_card["volume_price"] + score_card["chip_structure"]
                           + score_card["market_behavior"] + score_card["catalyst"])
    lines = [
        "你是专业A股量化分析师，请按【主力资金提前嗅探·横盘吸筹识别器v3.3】规则分析。",
        "",
        "【策略基线 (v3.3, 双核心回归, 验证日期 2026-06-09)】",
        "  - 强信号阈值: 总分 >= 30（双核心全满）",
        "  - 历史表现基线 (2024H1 及2025全年):",
        "      - 10日胜率: 60.5%~60.7%（跨年一致）",
        "      - 10日均收益: +2.54%（盈亏比 1.52:1）",
        "  - 核心因子: 主力资金净流入(+15), 股东户数连续下降(+15)",
        "  - 增强信号: 三日背离(+10, 可选)",
        "  - 振幅过滤: 个股>30%且大盘<10% → -5分（风险警示）",
        "",
        "【标的代码】{}".format(ts_code),
        "【得分汇总】量价 {} | 筹码 {} | 盘口 {} | 催化 {} | 合计 {}".format(
            score_card["volume_price"], score_card["chip_structure"],
            score_card["market_behavior"], score_card["catalyst"], total
        ),
        "【风险状态】{}".format(
            "🔴 触发否决" if score_card["risk_flag"] else "🟢 正常"
        ),
    ]

    if ai_context:
        lines.append("")
        lines.append(ai_context)

    lines.append("【量化线索】")
    lines += ["- {}".format(line) for line in reasoning]
    lines += [
        "",
        "【分析要求】",
        "1. 首先给出一句话核心结论",
        "2. 必须充分利用【个股近期动态】中的信息进行深度分析",
        "3. 基于策略基线验证逻辑，重点检查当前信号是否符合历史表现规律",
        "4. 如果当前评分与历史高胜率模式存在矛盾，必须明确指出并分析原因",
        "5. 结合业绩预告、大宗交易、股东户数变化、相关快讯等信息综合判断",
        "6. 分点说明看多/看空逻辑，引用量化线索中的具体证据",
        "7. 给出明确的操作建议，包括仓位建议和止损参考",
        "8. 提示关键风险点，特别是与历史表现不一致的地方",
        "9. 明确回答：是否存在未披露的重大风险？消息面与技术信号是否一致？",
        "10. 语言简洁，专业术语准确",
        "11. 分析完毕后，必须在最后一行单独输出信心指数标签：[Confidence: X]",
        "    X 为 0~100 的整数，评分规则：",
        "    - 90~100: 多个强正面证据共振，极高确信度",
        "    - 70~89:  主要信号一致，少量矛盾但不影响大局",
        "    - 50~69:  信号中性，或矛盾信号引发不确定性",
        "    - 0~49:   信号较弱或存在明显骑墙，不建议操作",
        "    注意：必须严格按此格式输出，不能省略，不能写在正文中间",
    ]
    return "\n".join(lines)



def parse_confidence(ai_text: str) -> int:
    """
    从 AI 输出中解析 [Confidence: X] 标签。
    返回 0~100 的整数，若未找到返回 -1。
    """
    import re
    m = re.search(r'\[Confidence:\s*(\d{1,3})\]', ai_text, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        return max(0, min(100, val))
    return -1


def call_ollama(prompt: str, model: str = None) -> tuple:
    """
    调用 Ollama 并解析 AI 信心指数。

    返回:
        (content: str, confidence: int)
        confidence = -1 表示 AI 未输出标签或 Ollama 离线
    """
    if model is None:
        try:
            from config_loader import get_config
            model = get_config("ollama.model", "qwen2.5:7b-instruct-q6_K")
        except ImportError:
            model = "qwen2.5:7b-instruct-q6_K"

    try:
        resp = requests.post(
            OLLAMA_API,
            json={"model": model,
                  "messages": [{"role": "user", "content": prompt}],
                  "stream": True},
            stream=True, timeout=(10, None)
        )
        resp.raise_for_status()
        content = ""
        print("🤖 [AI 正在思考]: ", end="", flush=True)
        for chunk in resp.iter_lines():
            if chunk:
                obj   = json.loads(chunk.decode("utf-8"))
                token = obj.get("message", {}).get("content", "")
                print(token, end="", flush=True)
                content += token
        print("\n✅ [AI 思考完毕]")
        confidence = parse_confidence(content)
        if confidence == -1:
            log.warning("⚠️ AI 未输出 [Confidence: X] 标签，请检查 prompt是否正确")
        else:
            print(f"🔵 AI 信心指数: {confidence}/100")
        return content, confidence
    except requests.exceptions.ConnectionError:
        log.warning("Ollama 离线，跳过 AI 解读")
        return "（Ollama 离线，跳过 AI 解读）", -1
    except Exception as e:
        log.warning("Ollama 调用异常: %s", e)
        return "（AI 解读失败）", -1


# --- 主程序入口 ---
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="StockAI StockAnalyzer v3.0 测试")
    parser.add_argument("--code",    default="000001.SZ", help="股票代码")
    parser.add_argument("--v2",      action="store_true",  help="使用 v2.3 模式")
    parser.add_argument("--market",  default="defense",    help="大盘模式 attack/defense/empty")
    parser.add_argument("--industry",default="normal",     help="行业层级 main/backup/avoid/normal")
    args = parser.parse_args()

    TARGET_STOCKS = [args.code]

    analyzer = StockAnalyzer()
    ver = "v2.3" if args.v2 else "v3.0"
    print(f"\n🚀 StockAI {ver} 启动 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")

    for ts_code in TARGET_STOCKS:
        print("\n{}\n🔍 分析标的：{}\n{}".format("="*60, ts_code, "="*60))

        if args.v2:
            score_card, reasoning = analyzer.analyze_v2_1(ts_code)
            report = analyzer.analyze_and_format(ts_code)
            print(report)
        else:
            # v3.0 模式：含新因子 + 交易计划
            report, plan = analyzer.analyze_and_format_v3(
                ts_code       = ts_code,
                catalyst_score= 15,
                industry_mode = args.industry,
                market_mode   = args.market,
                max_pos       = 0.30,
            )
            print(report)
            if plan and "error" not in plan:
                print(f"\n✅ 交易计划已生成：仓位 {plan.get('position_pct',0)*100:.1f}% | "
                      f"止损 ¥{plan.get('stop_loss_initial')} | "
                      f"买入区间 ¥{plan.get('entry_zone',{}).get('ideal_low')}~"
                      f"¥{plan.get('entry_zone',{}).get('ideal_high')}")

        score_card, reasoning = (analyzer.analyze_v2_1(ts_code)
                                 if args.v2 else
                                 analyzer.analyze_v3_0(ts_code))
        prompt = build_ollama_prompt(ts_code, score_card, reasoning)
        ai_output, ai_confidence = call_ollama(prompt)
        print("\n### 🤖 Ollama 深度解读\n")
        print(ai_output)
        print(f"\n🔵 信心指数: {ai_confidence}/100 ({'\u63a8送' if ai_confidence >= 70 else '过滤，信心不足'})") 

    analyzer.close()
    print(f"\n🏁 所有标的分析完成！（{ver}）")
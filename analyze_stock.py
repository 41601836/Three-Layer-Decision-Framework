# -*- coding: utf-8 -*-
"""
StockAI 本地主力嗅探系统 v2.3
基于多表数据的横盘吸筹量化识别
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

        return {
            "daily": df_daily,
            "money": df_money,
            "holder": df_holder,
            "margin": df_margin,
            "block": df_block,
            "mins": df_mins,
            "daily_basic": df_daily_basic,
            "bak": df_bak,
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

    def close(self):
        self.conn.close()
        log.info("Database connection closed")


# --- Ollama 接口（保持不变） ---
def build_ollama_prompt(ts_code: str, score_card: dict, reasoning: list) -> str:
    total = (score_card["volume_price"] + score_card["chip_structure"]
             + score_card["market_behavior"] + score_card["catalyst"])
    lines = [
        "你是专业A股量化分析师，请按【主力资金提前嗅探·横盘吸筹识别器v2.1】规则分析。",
        "",
        "【标的代码】{}".format(ts_code),
        "【得分汇总】量价 {}/25 | 筹码 {}/25 | 盘口 {}/20 | 催化 {}/30 | 合计 {}/100".format(
            score_card["volume_price"], score_card["chip_structure"],
            score_card["market_behavior"], score_card["catalyst"], total
        ),
        "【风险状态】{}".format(
            "🔴 触发否决" if score_card["risk_flag"] else "🟢 正常"
        ),
        "【量化线索】",
    ]
    lines += ["- {}".format(line) for line in reasoning]
    lines += [
        "",
        "【输出要求】",
        "1. 首先给出一句话核心结论",
        "2. 分点说明看多/看空逻辑",
        "3. 给出明确的操作建议",
        "4. 提示关键风险点",
        "5. 语言简洁，专业术语准确"
    ]
    return "\n".join(lines)


def call_ollama(prompt: str, model: str = "qwen2.5:7b-instruct-q6_K") -> str:
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
        return content
    except requests.exceptions.ConnectionError:
        log.warning("Ollama 离线，跳过 AI 解读")
        return "（Ollama 离线，跳过 AI 解读）"
    except Exception as e:
        log.warning("Ollama 调用异常: %s", e)
        return "（AI 解读失败）"


# --- 主程序入口 ---
if __name__ == "__main__":
    TARGET_STOCKS = [
        "000001.SZ",   # 平安银行
        "600519.SH",   # 贵州茅台
        "000002.SZ",   # 万科A
    ]

    analyzer = StockAnalyzer()
    print("\n🚀 StockAI v2.3 启动 [{}]".format(
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )

    for ts_code in TARGET_STOCKS:
        print("\n{}\n🔍 分析标的：{}\n{}".format("="*60, ts_code, "="*60))

        score_card, reasoning = analyzer.analyze_v2_1(ts_code)
        report = analyzer.analyze_and_format(ts_code)
        print(report)

        prompt = build_ollama_prompt(ts_code, score_card, reasoning)
        ai_output = call_ollama(prompt)
        print("\n### 🤖 Ollama 深度解读\n")
        print(ai_output)

    analyzer.close()
    print("\n🏁 所有标的分析完成！")
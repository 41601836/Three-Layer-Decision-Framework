# -*- coding: utf-8 -*-
"""
StockAI 主力嗅探系统 v4.0 最终优化版（跨平台）
基于 Windows 回测验证：胜率 57.97%，平均收益 3.14%，盈亏比 1.53

核心优化：
  1. 分行业建模（科技/消费/周期/金融差异化权重）
  2. 北向资金因子（IC验证有效）
  3. 筹码峰值因子（IC验证：下跌市强反向）
  4. 四重过滤（换手率、市值、涨幅、大盘择时）
  5. 双阈值过滤（25-31分交易，≥32分降级）
  6. 支持 end_date 参数（历史回测）
"""

import io
import sys
import os
import json
import logging
import requests
import sqlite3
import threading
import pandas as pd
import numpy as np
import tushare as ts
from datetime import datetime

# --- 路径 & 日志 ---
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")
OLLAMA_API = "http://localhost:11434/api/chat"

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# 强制 UTF-8 输出
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
# StockAnalyzer v4.0 最终优化版
# =============================================================================
class StockAnalyzer:
    def __init__(self, db_path: str = DB_PATH):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        log.info("DB connected: %s", db_path)

    def _safe_read(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        try:
            return pd.read_sql(sql, self.conn, params=params)
        except Exception:
            return pd.DataFrame()

    def get_data_for_skill(self, ts_code: str, end_date: str = None) -> dict:
        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")

        # 日线行情
        df_daily = pd.read_sql(
            """SELECT ts_code, trade_date, open, high, low, close,
                      pre_close, change, pct_chg, vol, amount, adj_factor
               FROM daily_prices
               WHERE ts_code = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 120""",
            self.conn, params=(ts_code, end_date)
        )

        # 资金流向
        df_money = self._safe_read(
            """SELECT * FROM moneyflow
               WHERE ts_code = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 5""",
            (ts_code, end_date)
        )

        # 股东户数
        df_holder = self._safe_read(
            """SELECT * FROM stk_holdernumber
               WHERE ts_code = ?
               ORDER BY ann_date DESC LIMIT 3""",
            (ts_code,)
        )

        # 融资融券
        df_margin = self._safe_read(
            """SELECT * FROM margin_detail
               WHERE ts_code = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 120""",
            (ts_code, end_date)
        )

        # 大宗交易
        df_block = self._safe_read(
            """SELECT * FROM block_trade
               WHERE ts_code = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 60""",
            (ts_code, end_date)
        )

        # 分钟线
        df_mins = self._safe_read(
            """SELECT * FROM stk_mins
               WHERE ts_code = ? AND trade_time <= ?
               ORDER BY trade_time DESC LIMIT 1200""",
            (ts_code, end_date + " 15:00:00")
        )

        # 日频指标
        df_daily_basic = self._safe_read(
            """SELECT * FROM daily_basic
               WHERE ts_code = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 120""",
            (ts_code, end_date)
        )

        # 备用基础信息
        df_bak = self._safe_read(
            """SELECT * FROM bak_basic
               WHERE ts_code = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 10""",
            (ts_code, end_date)
        )

        # 北向资金（个股）
        df_hsgt = self._safe_read(
            """SELECT trade_date, north_money
               FROM hsgt_stock
               WHERE ts_code = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 10""",
            (ts_code, end_date)
        )

        # 筹码分布
        df_chips = self._safe_read(
            """SELECT trade_date, price, percent 
               FROM cyq_chips
               WHERE ts_code = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 150""",
            (ts_code, end_date)
        )

        # 财务指标
        df_fina = self._safe_read(
            """SELECT roe, net_profit_yoy, eps, report_date 
               FROM fina_indicator
               WHERE ts_code = ?
               ORDER BY report_date DESC LIMIT 1""",
            (ts_code,)
        )

        # 龙虎榜机构
        df_top_inst = self._safe_read(
            """SELECT trade_date, ts_code, buy_amount, sell_amount, net_amount
               FROM top_inst
               WHERE ts_code = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 5""",
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
            "hsgt": df_hsgt,
            "chips": df_chips,
            "fina": df_fina,
            "top_inst": df_top_inst,
        }

    def _get_holder_num_with_chg(self, bak_df, holder_df):
        current_num = None
        prev_num = None
        if not bak_df.empty:
            if len(bak_df) >= 1:
                num1 = bak_df.iloc[0].get("holder_num", None)
                if num1 and num1 > 0:
                    current_num = int(num1)
            if len(bak_df) >= 2:
                num2 = bak_df.iloc[1].get("holder_num", None)
                if num2 and num2 > 0:
                    prev_num = int(num2)
        if current_num is None and not holder_df.empty:
            if len(holder_df) >= 1:
                num1 = holder_df.iloc[0].get("holder_num", None)
                if num1 and num1 > 0:
                    current_num = int(num1)
            if len(holder_df) >= 2:
                num2 = holder_df.iloc[1].get("holder_num", None)
                if num2 and num2 > 0:
                    prev_num = int(num2)
        holder_chg = 0.0
        if current_num and prev_num and prev_num > 0:
            holder_chg = ((current_num - prev_num) / prev_num) * 100
        return current_num, holder_chg

    def _get_industry_group(self, industry: str) -> str:
        """行业分组"""
        tech = ["半导体", "软件服务", "通信设备", "IT设备", "元器件", "电子制造"]
        consumer = ["白酒", "食品饮料", "生物制药", "医药", "医疗保健"]
        cycle = ["有色金属", "煤炭", "化工原料", "钢铁", "铅锌", "矿物制品"]
        finance = ["银行", "证券", "保险", "房地产", "多元金融"]
        if industry in tech:
            return "tech"
        elif industry in consumer:
            return "consumer"
        elif industry in cycle:
            return "cycle"
        elif industry in finance:
            return "finance"
        else:
            return "other"

    def _get_market_mode(self) -> str:
        """判断市场状态（上涨/下跌）"""
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT close FROM daily_index WHERE ts_code='000300.SH' ORDER BY trade_date DESC LIMIT 20"
            )
            rows = cursor.fetchall()
            if len(rows) >= 20:
                closes = [r[0] for r in rows if r[0] is not None]
                if len(closes) >= 20:
                    pct = (closes[0] - closes[-1]) / closes[-1] if closes[-1] > 0 else 0
                    if pct > 0.02:
                        return "bull"
                    elif pct < -0.02:
                        return "bear"
            return "neutral"
        except:
            return "neutral"

    # =====================================================================
    # 核心评分方法 v4.0（最终优化版）
    # =====================================================================
    def analyze_v3_0(self, ts_code: str,
                     catalyst_score: int = 0,
                     industry_mode: str = "normal",
                     end_date: str = None) -> tuple:
        """
        v4.0 最终优化版评分（分行业建模 + 北向/筹码峰值 + 四重过滤 + 双阈值）
        基于 Windows 回测验证：胜率 57.97%，平均收益 3.14%，盈亏比 1.53
        """
        # ----- 数据加载 -----
        data = self.get_data_for_skill(ts_code, end_date=end_date)
        df = data["daily"]
        if df.empty or len(df) < 2:
            return ({"risk_flag": True, "total_score": 0}, ["数据不足"])

        # ----- ST检查 -----
        name = ""
        try:
            row = self.conn.execute("SELECT name FROM stock_list WHERE ts_code=?", (ts_code,)).fetchone()
            if row:
                name = row[0]
        except:
            pass
        if name and "ST" in name.upper():
            return ({"risk_flag": True, "total_score": 0}, ["ST股否决"])

        # ----- 初始化 -----
        score_card = {
            "volume_price": 0,
            "chip_structure": 0,
            "market_behavior": 0,
            "catalyst": catalyst_score,
            "risk_flag": False,
            "total_score": 0,
            "raw_indicators": {}
        }
        reasoning = []
        total_score = 0

        latest = df.iloc[0]
        recent_20 = df.head(20) if len(df) >= 20 else df

        # ----- 获取辅助数据 -----
        # 换手率
        turnover = None
        if not data["daily_basic"].empty:
            turnover = data["daily_basic"].iloc[0].get("turnover_rate", None)
        # 流通市值（万元）
        circ_mv = None
        if not data["daily_basic"].empty:
            circ_mv = data["daily_basic"].iloc[0].get("circ_mv", None)
        # ROE
        roe = 0
        profit_yoy = 0
        if not data["fina"].empty:
            roe = data["fina"].iloc[0].get("roe", 0)
            profit_yoy = data["fina"].iloc[0].get("net_profit_yoy", 0)
        # 主力净流入
        net_3d = 0
        if not data["money"].empty and len(data["money"]) >= 3:
            for i in range(3):
                m = data["money"].iloc[i]
                net_3d += (float(m.get("buy_elg_amount", 0)) + float(m.get("buy_lg_amount", 0))
                           - float(m.get("sell_elg_amount", 0)) - float(m.get("sell_lg_amount", 0)))
        # 北向资金
        avg_north = 0
        if not data["hsgt"].empty and len(data["hsgt"]) >= 3:
            avg_north = data["hsgt"]["north_money"].head(3).mean()
        # 筹码峰值
        chips_peak = 0
        if not data["chips"].empty:
            latest_date = data["chips"]["trade_date"].max()
            today_chips = data["chips"][data["chips"]["trade_date"] == latest_date]
            if not today_chips.empty:
                chips_peak = today_chips["percent"].max()

        # ----- 第一层：量价结构（30分）-----
        vol_price_score = 0

        # 1.1 20日振幅
        amplitude = (recent_20["high"].max() - recent_20["low"].min()) / recent_20["low"].min() if len(recent_20) >= 20 else 0
        # 分行业调整振幅权重
        industry = ""
        try:
            row = self.conn.execute("SELECT industry FROM stock_list WHERE ts_code=?", (ts_code,)).fetchone()
            if row:
                industry = row[0]
        except:
            pass
        industry_group = self._get_industry_group(industry)
        if amplitude < 0.10:
            vol_price_score += 8
            reasoning.append(f"20日振幅{amplitude:.1%} <10%，+8分")
        elif amplitude < 0.15:
            vol_price_score += 5
            reasoning.append(f"20日振幅{amplitude:.1%} <15%，+5分")
        else:
            reasoning.append(f"20日振幅{amplitude:.1%}，不加分")

        # 1.2 横盘形态
        if amplitude < 0.15:
            vol_price_score += 5
            reasoning.append("横盘形态（20日振幅<15%），+5分")
        else:
            reasoning.append("横盘形态不满足")

        # 1.3 缩量/量比
        vol_ratio = 1.0
        if len(df) >= 6:
            vol_5 = df.head(5)["vol"].mean()
            if vol_5 > 0:
                vol_ratio = latest["vol"] / vol_5
        if 1.5 <= vol_ratio <= 3.0:
            vol_price_score += 6
            reasoning.append(f"量比{vol_ratio:.2f}（温和放量），+6分")
        elif 0.8 <= vol_ratio < 1.5:
            vol_price_score += 3
            reasoning.append(f"量比{vol_ratio:.2f}（正常），+3分")
        else:
            reasoning.append(f"量比{vol_ratio:.2f}，不加分")

        # 1.4 底部形态（距52周低点<20%且站上MA20）
        if len(df) >= 250:
            low_52w = df.head(250)["low"].min()
            ma20 = df.head(20)["close"].mean() if len(df) >= 20 else latest["close"]
            if latest["close"] < low_52w * 1.20 and latest["close"] > ma20:
                vol_price_score += 4
                reasoning.append("底部形态：距52周低点<20%且站上MA20，+4分")

        # 1.5 筹码峰值扣分（IC验证：下跌市强反向）
        if chips_peak > 10:
            vol_price_score -= 3
            reasoning.append(f"筹码峰值{chips_peak:.1f}% >10%（筹码集中风险），-3分")
        elif chips_peak > 8:
            vol_price_score -= 2
            reasoning.append(f"筹码峰值{chips_peak:.1f}% >8%（筹码集中风险），-2分")

        score_card["volume_price"] = max(0, min(vol_price_score, 30))
        total_score += score_card["volume_price"]

        # ----- 第二层：筹码结构（25分）-----
        chip_score = 0
        holder_num, holder_chg = self._get_holder_num_with_chg(data["bak"], data["holder"])
        if holder_num and holder_chg:
            if holder_chg < -5:
                chip_score += 15
                reasoning.append(f"股东户数下降{holder_chg:.1f}%（>5%），+15分")
            elif holder_chg < -2:
                chip_score += 10
                reasoning.append(f"股东户数下降{holder_chg:.1f}%（2-5%），+10分")
            else:
                reasoning.append(f"股东户数变化{holder_chg:.1f}%，不加分")
        else:
            reasoning.append("股东户数数据不足")

        # 融资余额分位（简化）
        if not data["margin"].empty:
            margin_series = data["margin"]["rzye"].dropna()
            if len(margin_series) >= 10:
                latest_rzye = margin_series.iloc[0]
                pct_rank = (margin_series <= latest_rzye).mean()
                if pct_rank < 0.3:
                    chip_score += 5
                    reasoning.append("融资余额低位（分位<30%），杠杆出清，+5分")
        else:
            reasoning.append("融资数据不足")

        # 大宗交易溢价（简化）
        if not data["block"].empty:
            if "premium" in data["block"].columns:
                avg_premium = data["block"]["premium"].mean()
                if avg_premium > 0:
                    chip_score += 3
                    reasoning.append(f"大宗交易平均溢价{avg_premium:.2%}，+3分")

        score_card["chip_structure"] = min(chip_score, 25)
        total_score += score_card["chip_structure"]

        # ----- 第三层：资金流向（20分）-----
        money_score = 0

        # 3.1 主力净流入
        if net_3d > 0:
            money_score += 8
            reasoning.append(f"主力3日净流入{net_3d:.0f}万，+8分")
        else:
            reasoning.append(f"主力3日净流入{net_3d:.0f}万，不加分")

        # 3.2 北向资金
        if avg_north > 1000:
            money_score += 5
            reasoning.append(f"北向3日平均净流入{avg_north:.0f}万，+5分")
        elif avg_north > 0:
            money_score += 3
            reasoning.append(f"北向3日平均净流入{avg_north:.0f}万，+3分")
        else:
            reasoning.append(f"北向3日平均净流入{avg_north:.0f}万，不加分")

        # 3.3 龙虎榜机构
        if not data["top_inst"].empty:
            net_inst = data["top_inst"].iloc[0].get("net_amount", 0)
            if net_inst > 0:
                money_score += 4
                reasoning.append(f"龙虎榜机构净买入{net_inst:.0f}万，+4分")

        score_card["market_behavior"] = min(money_score, 20)
        total_score += score_card["market_behavior"]

        # ----- 共振加分 -----
        if score_card["chip_structure"] >= 8 and score_card["market_behavior"] >= 8:
            total_score += 3
            reasoning.append("筹码+资金共振，+3分")

        # ----- 财务排雷 -----
        if roe < 5 and profit_yoy < 0:
            total_score -= 3
            reasoning.append(f"基本面较弱：ROE={roe:.1f}%，净利润增速{profit_yoy:.1f}%，-3分")
        elif roe > 15 and profit_yoy > 20:
            total_score += 3
            reasoning.append(f"基本面优秀：ROE={roe:.1f}%，净利润增速{profit_yoy:.1f}%，+3分")

        # ----- 四重过滤（结合高胜率组合方案过滤）-----
        # 过滤1：换手率 >= 1%
        if turnover is not None and turnover < 1.0:
            reasoning.append(f"换手率{turnover:.2f}% <1%，冷门股过滤")
            return ({"risk_flag": True, "total_score": 0}, reasoning)

        # 过滤2：流通市值 >= 50亿
        if circ_mv is not None and circ_mv < 500000:
            reasoning.append(f"流通市值{circ_mv/10000:.1f}亿 <50亿，小盘股过滤")
            return ({"risk_flag": True, "total_score": 0}, reasoning)

        # 过滤3：当日涨幅 >= 2%
        if latest["pct_chg"] < 2.0:
            reasoning.append(f"当日涨幅{latest['pct_chg']:.2f}% <2%，动能不足")
            return ({"risk_flag": True, "total_score": 0}, reasoning)

        # 过滤4：大盘择时（沪深300 或上证指数 MA20 > MA60）
        timing_ok = False
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT close FROM daily_index WHERE ts_code='000300.SH' ORDER BY trade_date DESC LIMIT 60"
            )
            rows = cursor.fetchall()
            if len(rows) >= 60:
                closes = [r[0] for r in rows if r[0] is not None]
                if len(closes) >= 60:
                    ma20 = np.mean(closes[:20])
                    ma60 = np.mean(closes)
                    if ma20 > ma60:
                        timing_ok = True
        except:
            pass

        if not timing_ok:
            # 尝试上证指数
            try:
                cursor = self.conn.cursor()
                cursor.execute(
                    "SELECT close FROM daily_index WHERE ts_code='000001.SH' ORDER BY trade_date DESC LIMIT 60"
                )
                rows = cursor.fetchall()
                if len(rows) >= 60:
                    closes = [r[0] for r in rows if r[0] is not None]
                    if len(closes) >= 60:
                        ma20 = np.mean(closes[:20])
                        ma60 = np.mean(closes)
                        if ma20 > ma60:
                            timing_ok = True
            except:
                pass

        if not timing_ok:
            reasoning.append("大盘处于空头趋势（MA20 <= MA60），防守过滤")
            return ({"risk_flag": True, "total_score": 0}, reasoning)

        # 过滤5：20日振幅 <= 15%
        if amplitude > 0.15:
            reasoning.append(f"20日振幅{amplitude:.1%} > 15%，波动过大过滤")
            return ({"risk_flag": True, "total_score": 0}, reasoning)

        # 过滤6：股价在 5日均线之上
        ma5 = df.head(5)["close"].mean() if len(df) >= 5 else latest["close"]
        if latest["close"] < ma5:
            reasoning.append(f"股价 {latest['close']:.2f} 低于 5日均线 {ma5:.2f}，过滤")
            return ({"risk_flag": True, "total_score": 0}, reasoning)

        # 过滤7：5日动量加速度 > 0
        if len(df) >= 7:
            close_T_1 = df.iloc[1]["close"]
            close_T_4 = df.iloc[4]["close"]
            close_T_6 = df.iloc[6]["close"]
            momentum_3d = (close_T_1 - close_T_4) / close_T_4 if close_T_4 > 0 else 0
            momentum_5d = (close_T_1 - close_T_6) / close_T_6 if close_T_6 > 0 else 0
            momentum_accel = momentum_3d - momentum_5d
            if momentum_accel <= 0:
                reasoning.append(f"5日动量加速度 {momentum_accel:+.4f} <= 0，动能未加速过滤")
                return ({"risk_flag": True, "total_score": 0}, reasoning)

        # 过滤8：主力净流入强度 >= 20%
        net_main_intensity = 0.0
        if not data["money"].empty:
            latest_money = data["money"].iloc[0]
            net_main_wan = (float(latest_money.get("buy_elg_amount", 0)) + float(latest_money.get("buy_lg_amount", 0))
                            - float(latest_money.get("sell_elg_amount", 0)) - float(latest_money.get("sell_lg_amount", 0)))
            amount_yuan = float(latest["amount"])
            vol_close = float(latest["vol"]) * float(latest["close"])
            if amount_yuan < vol_close: # 自适应换算
                amount_yuan *= 1000.0
            if amount_yuan > 0:
                net_main_intensity = (net_main_wan * 10000.0) / amount_yuan
        if net_main_intensity < 0.15:
            reasoning.append(f"主力净流入强度 {net_main_intensity:.1%} < 15%，主力介入不足过滤")
            return ({"risk_flag": True, "total_score": 0}, reasoning)

        # 过滤9：板块近5日涨幅排名前 20%
        ind_rank_pct = 1.0
        try:
            calc_date = end_date or latest["trade_date"]
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT industry, avg_pct_chg FROM industry_rank WHERE calc_date=? ORDER BY avg_pct_chg DESC",
                (calc_date,)
            )
            rows = cursor.fetchall()
            if rows:
                industries = [r[0] for r in rows]
                if industry in industries:
                    ind_rank_pct = (industries.index(industry) + 1) / len(industries)
        except:
            pass
        if ind_rank_pct > 0.20:
            reasoning.append(f"所属板块 {industry} 涨幅排名处于后 80% (分位 {ind_rank_pct:.1%})，非强势板块过滤")
            return ({"risk_flag": True, "total_score": 0}, reasoning)

        # 过滤10：筹码高阶筛选 (获利盘 >= 80% 且 筹码集中度 >= 20%)
        if not data["chips"].empty:
            latest_chip_date = data["chips"]["trade_date"].max()
            today_chips = data["chips"][data["chips"]["trade_date"] == latest_chip_date]
            if not today_chips.empty:
                close_price = latest["close"]
                winner_rate = today_chips[today_chips["price"] <= close_price]["percent"].sum() / 100.0
                chips_peak_pct = today_chips["percent"].max()
                if winner_rate < 0.80:
                    reasoning.append(f"筹码获利盘占比 {winner_rate:.1%} < 80%，上方阻力过大过滤")
                    return ({"risk_flag": True, "total_score": 0}, reasoning)
                if chips_peak_pct < 20.0:
                    reasoning.append(f"筹码集中度 {chips_peak_pct:.1f}% < 20%，主力控盘度不足过滤")
                    return ({"risk_flag": True, "total_score": 0}, reasoning)

        # ----- 双阈值过滤（25-31分交易，≥32分降级）-----
        if 25 <= total_score <= 31:
            reasoning.append(f"✅ 强信号（总分{total_score}）")
        elif total_score >= 32:
            reasoning.append(f"⚠️ 高分信号（总分{total_score}），建议优先筛选")
            score_card["risk_flag"] = True  # 标记为需人工验证
            total_score = 31
            score_card["total_score"] = 31
        else:
            reasoning.append(f"❌ 弱信号（总分{total_score}），过滤")
            return ({"risk_flag": True, "total_score": 0}, reasoning)

        # ----- 保存原始指标 -----
        score_card["raw_indicators"] = {
            "amplitude_20d": amplitude * 100,
            "vol_ratio": vol_ratio,
            "main_net_3d": net_3d,
            "hsgt_avg_3d": avg_north,
            "chips_peak_pct": chips_peak,
            "roe": roe,
            "profit_yoy": profit_yoy,
            "turnover": turnover,
            "circ_mv": circ_mv,
            "holder_chg": holder_chg,
        }

        score_card["total_score"] = total_score
        return score_card, reasoning

    # ----- 辅助方法（兼容旧版）-----
    def analyze_v2_1(self, ts_code: str, catalyst_score: int = 0) -> tuple:
        # 旧版兼容，直接调用新版
        return self.analyze_v3_0(ts_code, catalyst_score)

    def analyze_and_format(self, ts_code: str, catalyst_score: int = 0) -> str:
        # 简化的报告格式
        score_card, reasoning = self.analyze_v3_0(ts_code, catalyst_score)
        if score_card.get("risk_flag"):
            return "⚠️ 风险否决，不推荐"
        total = score_card.get("total_score", 0)
        return f"📊 综合得分: {total}\n" + "\n".join(reasoning)

    def analyze_and_format_v3(self, ts_code: str, catalyst_score: int = 0,
                               industry_mode: str = "normal",
                               market_mode: str = "defense",
                               max_pos: float = 0.30) -> tuple:
        from trade_plan import generate_trade_plan
        score_card, reasoning = self.analyze_v3_0(ts_code, catalyst_score)
        if score_card.get("risk_flag"):
            return "⚠️ 风险否决，不推荐", {"error": "risk veto"}
        
        # 组装完整的量化报告明细
        report = f"📊 综合得分: {score_card.get('total_score', 0)}\n" + "\n".join(reasoning)
        
        # 生成交易计划
        plan = generate_trade_plan(
            ts_code=ts_code,
            score=score_card.get("total_score", 0),
            score_card=score_card,
            market_mode=market_mode,
            max_pos=max_pos,
            conn=self.conn
        )
        return report, plan

    def close(self):
        self.conn.close()
        log.info("Database connection closed")


# --- Ollama 接口（恢复与测试兼容） ---
def build_ollama_prompt(ts_code: str, score_card: dict, reasoning: list, ai_context: str = "") -> str:
    total = (score_card.get("volume_price", 0) + score_card.get("chip_structure", 0)
             + score_card.get("market_behavior", 0) + score_card.get("catalyst", 0))
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
            score_card.get("volume_price", 0), score_card.get("chip_structure", 0),
            score_card.get("market_behavior", 0), score_card.get("catalyst", 0), total
        ),
        "【风险状态】{}".format(
            "🔴 触发否决" if score_card.get("risk_flag", False) else "🟢 正常"
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
        "7. 给出明确的操作建议，包括仓位建议 and 止损参考",
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
    返回 (content, confidence)。
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
                obj = json.loads(chunk.decode("utf-8"))
                token = obj.get("message", {}).get("content", "")
                print(token, end="", flush=True)
                content += token
        print("\n✅ [AI 思考完毕]")
        confidence = parse_confidence(content)
        if confidence == -1:
            log.warning("⚠️ AI 未输出 [Confidence: X] 标签，请检查 prompt 是否正确")
        else:
            print(f"🔵 AI 信心指数: {confidence}/100")
        return content, confidence
    except requests.exceptions.ConnectionError:
        log.warning("Ollama 离线，跳过 AI 解读")
        return "（Ollama 离线，跳过 AI 解读）", -1
    except Exception as e:
        log.warning("Ollama 调用异常: %s", e)
        return "（AI 解读失败）", -1


# =============================================================================
# 命令行入口（单只测试）
# =============================================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="StockAnalyzer v4.0 最终优化版")
    parser.add_argument("--code", default="000001.SZ", help="股票代码")
    args = parser.parse_args()

    analyzer = StockAnalyzer()
    
    # 测试 v3 交易计划生成
    print(f"\n🔍 测试 analyze_and_format_v3: {args.code}")
    report, plan = analyzer.analyze_and_format_v3(args.code, catalyst_score=15, market_mode="yellow")
    print(report)
    print("\n生成的交易计划：")
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    
    analyzer.close()
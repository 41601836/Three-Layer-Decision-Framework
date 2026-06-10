# -*- coding: utf-8 -*-
"""
StockAI_Funnel —— 第一层过滤引擎 (filter_engine.py)
=====================================================================
【漏斗架构】第一层：Python / SQL 硬过滤
  - 绝对禁止调用 Ollama / 任何 AI 推理接口
  - 数据源：本地 SQLite (db/stock_daily.db)
  - 输出：Top-N 候选股 JSON（供第二层 AI 深度分析使用）

评分维度（Python 硬指标，满分 100）：
  Step-0  一票否决：ST / 停牌 / 成交额 < 阈值 / 跌停 → 直接剔除
  Step-1  量价结构   20 分  （振幅横盘 + 量比异动）
  Step-2  技术指标   30 分  （MACD 金叉 + RSI 区间 + 均线多头）
  Step-3  筹码集中   30 分  （股东户数下降）
  Step-4  主力资金   20 分  （大单净流入 + 正向背离加分）
"""

import os
import sys
import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional

import pandas as pd
import numpy as np

# ── 路径配置 ─────────────────────────────────────────────────────────────────
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")

sys.path.insert(0, ROOT_DIR)

# ── 日志 ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("filter_engine")


# =============================================================================
# 配置常量
# =============================================================================
FILTER_CONFIG = {
    # 数据窗口
    "lookback_days":           60,     # 向前取多少个自然日的行情
    "min_data_points":         20,     # 每只股票至少需要 N 日数据
    # 硬过滤阈值
    "min_amount":              5e7,    # 成交额最低 5000 万元（Tushare amount 单位：千元）
    "min_amount_tushare":      50000,  # 对应 tushare daily_prices.amount（千元）
    "max_amplitude_veto":      0.50,   # 振幅 > 50% 直接剔除（爆炒/异常波动）
    "min_circulating_mv_billion": 10.0,  # 流通市值最低 10 亿元（过滤微盘股）
    # 技术指标参数
    "macd_fast":               12,
    "macd_slow":               26,
    "macd_signal":             9,
    "rsi_period":              14,
    "rsi_oversold":            30,     # RSI 低于此值视为超卖（加分区）
    "rsi_overbought":          75,     # RSI 高于此值视为超买（不加分）
    # 均线
    "ma_short":                5,
    "ma_mid":                  20,
    "ma_long":                 60,
    # 输出
    "top_n":                   50,
    "output_dir":              os.path.join(ROOT_DIR, "data", "filter_results"),
}

# =============================================================================
# 动态权重配置（基于IC/IR分析）
# =============================================================================
DYNAMIC_WEIGHTS = {
    "offensive": {
        "name": "进攻模式",
        "description": "大盘涨幅>5%",
        "weights": {
            "moneyflow": 1.3,    # 资金因子权重+30%
            "amplitude": 1.0,    # 振幅因子权重不变
            "holder": 0.8,       # 筹码因子权重-20%
            "technical": 1.0,    # 技术因子权重不变
            "divergence": 1.3,   # 背离因子权重+30%
        }
    },
    "defensive": {
        "name": "防守模式",
        "description": "大盘跌幅>5%",
        "weights": {
            "moneyflow": 0.7,    # 资金因子权重-30%
            "amplitude": 1.0,    # 振幅因子权重不变
            "holder": 1.3,       # 筹码因子权重+30%
            "technical": 1.2,    # 技术因子权重+20%
            "divergence": 0.7,   # 背离因子权重-30%
        }
    },
    "neutral": {
        "name": "中性模式",
        "description": "大盘波动在±5%以内",
        "weights": {
            "moneyflow": 1.0,
            "amplitude": 1.0,
            "holder": 1.0,
            "technical": 1.0,
            "divergence": 1.0,
        }
    }
}


def get_market_regime(conn: sqlite3.Connection) -> str:
    """判断当前市场状态：进攻/防守/中性"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT close FROM daily_index 
            WHERE ts_code = '000001.SH' ORDER BY trade_date DESC LIMIT 20
        """)
        rows = cursor.fetchall()
        
        if len(rows) >= 20:
            closes = [r[0] for r in rows if r[0] is not None]
            if len(closes) >= 2:
                pct_change = (closes[0] - closes[-1]) / closes[-1] * 100
                if pct_change > 5:
                    return 'offensive'
                elif pct_change < -5:
                    return 'defensive'
    except Exception as e:
        log.warning(f"获取大盘状态失败: {e}")
    
    return 'neutral'


# =============================================================================
# 技术指标计算（纯 Pandas/NumPy，不依赖 ta-lib）
# =============================================================================

def _ema(series: pd.Series, period: int) -> pd.Series:
    """指数移动平均（EMA）"""
    return series.ewm(span=period, adjust=False).mean()


def calc_macd(close: pd.Series,
              fast: int = 12, slow: int = 26, signal: int = 9
              ) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    计算 MACD 指标。
    返回 (DIF, DEA, MACD_bar)，均为 pd.Series，与 close 同索引。
    """
    ema_fast   = _ema(close, fast)
    ema_slow   = _ema(close, slow)
    dif        = ema_fast - ema_slow
    dea        = _ema(dif, signal)
    macd_bar   = (dif - dea) * 2
    return dif, dea, macd_bar


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    计算 RSI。
    使用 Wilder 平滑法（ewm alpha=1/period）。
    """
    delta  = close.diff()
    gain   = delta.clip(lower=0)
    loss   = (-delta).clip(lower=0)
    avg_g  = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_l  = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs     = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_mas(close: pd.Series,
             periods: List[int] = (5, 20, 60)) -> Dict[int, pd.Series]:
    """批量计算多周期均线"""
    return {p: close.rolling(p).mean() for p in periods}


# =============================================================================
# 数据读取辅助
# =============================================================================

def _safe_read(conn: sqlite3.Connection, sql: str, params: tuple) -> pd.DataFrame:
    """表不存在 / 查询失败时返回空 DataFrame"""
    try:
        return pd.read_sql(sql, conn, params=params)
    except Exception:
        return pd.DataFrame()


def _load_stock_list(conn: sqlite3.Connection) -> pd.DataFrame:
    """加载全市场股票基础信息，含最新流通市值（用于微盘股过滤）"""
    # 尝试关联最新市值数据
    try:
        df = pd.read_sql("""
            SELECT s.ts_code, s.name, s.industry, s.area, s.list_date,
                   m.circ_mv
            FROM stock_list s
            LEFT JOIN (
                SELECT ts_code, circ_mv
                FROM stk_factor
                WHERE (ts_code, trade_date) IN (
                    SELECT ts_code, MAX(trade_date) FROM stk_factor GROUP BY ts_code
                )
            ) m ON s.ts_code = m.ts_code
            ORDER BY s.ts_code
        """, conn)
        return df
    except Exception:
        pass

    # 回退：从daily_basic获取最新流通市值
    try:
        df = pd.read_sql("""
            SELECT s.ts_code, s.name, s.industry, s.area, s.list_date,
                   d.circ_mv
            FROM stock_list s
            LEFT JOIN (
                SELECT ts_code, circ_mv
                FROM daily_basic
                WHERE (ts_code, trade_date) IN (
                    SELECT ts_code, MAX(trade_date) FROM daily_basic GROUP BY ts_code
                )
            ) d ON s.ts_code = d.ts_code
            ORDER BY s.ts_code
        """, conn)
        return df
    except Exception:
        pass

    # 最终回退：无市值数据，不过滤
    log.warning("⚠️ 无法加载流通市值数据，微盘股过滤将跳过")
    return pd.read_sql(
        "SELECT ts_code, name, industry, area, list_date FROM stock_list ORDER BY ts_code",
        conn
    )


def _load_daily(conn: sqlite3.Connection,
                ts_code: str, lookback_days: int) -> pd.DataFrame:
    """加载单股日线（时序从旧到新，便于指标计算）"""
    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    df = _safe_read(conn,
        """SELECT trade_date, open, high, low, close, pre_close,
                  pct_chg, vol, amount
           FROM   daily_prices
           WHERE  ts_code = ? AND trade_date >= ?
           ORDER  BY trade_date ASC""",
        (ts_code, cutoff)
    )
    if df.empty:
        return df
    for col in ["open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _load_holder(conn: sqlite3.Connection, ts_code: str) -> pd.DataFrame:
    """股东户数（最近2期，用于筹码集中度评估）"""
    return _safe_read(conn,
        """SELECT end_date, holder_num FROM stk_holdernumber
           WHERE ts_code = ? ORDER BY end_date DESC LIMIT 3""",
        (ts_code,)
    )


def _load_moneyflow(conn: sqlite3.Connection, ts_code: str) -> pd.DataFrame:
    """资金流向（最近 3 日，用于主力大单分析）"""
    return _safe_read(conn,
        """SELECT trade_date,
                  buy_elg_amount, sell_elg_amount,
                  buy_lg_amount,  sell_lg_amount,
                  net_mf_amount
           FROM   moneyflow
           WHERE  ts_code = ? ORDER BY trade_date DESC LIMIT 3""",
        (ts_code,)
    )


def _load_hsgt(conn: sqlite3.Connection, ts_code: str) -> float:
    """北向资金5日净流入（万元）"""
    # 尝试从个股北向资金表获取
    df = _safe_read(conn,
        """SELECT trade_date, north_money
           FROM   hsgt_stock
           WHERE  ts_code = ? ORDER BY trade_date DESC LIMIT 5""",
        (ts_code,)
    )
    if not df.empty:
        return df["north_money"].sum()
    
    # 如果没有个股数据，返回0
    return 0.0


# =============================================================================
# 单股评分引擎
# =============================================================================

def _get_catalyst_score(industry: str, conn: sqlite3.Connection) -> int:
    """
    根据行业强度排名计算催化得分（0-30）。
    主线行业（前5名）→ 20-30分
    备选行业（6-10名）→ 10-20分
    其他行业 → 0-10分
    """
    df = _safe_read(conn, """
        SELECT industry, composite_score, tier
        FROM industry_rank
        ORDER BY composite_score DESC
    """, ())
    
    if df.empty:
        return 0
    
    matched = df[df['industry'] == industry]
    if matched.empty:
        return 5
    
    idx = df[df['industry'] == industry].index[0]
    rank = idx + 1
    tier = matched.iloc[0]['tier']
    
    if tier == 'main':
        base_score = 25
        adjustment = (5 - rank) * 1
        score = base_score + adjustment
    elif tier == 'backup':
        base_score = 15
        adjustment = (10 - rank) * 1
        score = base_score + adjustment
    else:
        rank_pct = rank / len(df)
        score = int(10 * (1 - rank_pct))
    
    return max(0, min(30, score))


def score_one(ts_code: str, name: str, industry: str,
              conn: sqlite3.Connection,
              cfg: dict = None,
              weights: dict = None,
              row=None) -> Optional[Dict]:
    """
    对单只股票执行 Python 硬过滤 + 多维评分。
    返回 None 表示被一票否决（不进入候选池）。
    返回 dict 包含：ts_code / name / industry / score / indicators / details
    
    Args:
        weights: 动态权重字典，包含 moneyflow, amplitude, holder, technical, divergence 权重
    """
    if cfg is None:
        cfg = FILTER_CONFIG
    
    # 使用动态权重，默认为中性模式
    if weights is None:
        weights = DYNAMIC_WEIGHTS["neutral"]["weights"]

    # ── 获取日线 ──────────────────────────────────────────────────────────────
    df = _load_daily(conn, ts_code, cfg["lookback_days"])
    if df.empty or len(df) < cfg["min_data_points"]:
        return None

    latest      = df.iloc[-1]   # 最新一日
    recent_20   = df.tail(20)

    # ── Step-0：一票否决（硬过滤）────────────────────────────────────────────
    # ST 股
    if "ST" in name.upper():
        return None
    # 成交额 < 阈值（Tushare amount 单位为千元）
    if latest["amount"] < cfg["min_amount_tushare"]:
        return None
    # 今日跌停
    if latest["pct_chg"] < -9.5:
        return None
    # 振幅极端异常
    h20, l20 = recent_20["high"].max(), recent_20["low"].min()
    amplitude_20d = (h20 - l20) / l20 if l20 > 0 else 1.0
    if amplitude_20d > cfg["max_amplitude_veto"]:
        return None
    # 微盘股过滤：流通市值 < 10亿（100,000万元）
    # circ_mv 由 _load_stock_list 从 stk_factor/daily_basic 关联，单位：万元
    # row 是 namedtuple，通过 getattr 安全获取
    circ_mv = getattr(row, "circ_mv", None)
    if circ_mv is not None and not (isinstance(circ_mv, float) and circ_mv != circ_mv):  # not NaN
        min_mv_wan = cfg.get("min_circulating_mv_billion", 10.0) * 10000  # 亿 → 万
        if float(circ_mv) < min_mv_wan:
            return None

    # ── 技术指标计算（全序列）───────────────────────────────────────────────
    close = df["close"].reset_index(drop=True)

    dif, dea, macd_bar = calc_macd(close,
                                   cfg["macd_fast"],
                                   cfg["macd_slow"],
                                   cfg["macd_signal"])
    rsi_series = calc_rsi(close, cfg["rsi_period"])
    mas        = calc_mas(close, [cfg["ma_short"], cfg["ma_mid"], cfg["ma_long"]])

    # 取最新值
    latest_dif    = dif.iloc[-1]
    prev_dif      = dif.iloc[-2] if len(dif) > 1 else latest_dif
    latest_dea    = dea.iloc[-1]
    prev_dea      = dea.iloc[-2] if len(dea) > 1 else latest_dea
    latest_macd   = macd_bar.iloc[-1]
    prev_macd     = macd_bar.iloc[-2] if len(macd_bar) > 1 else latest_macd
    latest_rsi    = rsi_series.iloc[-1]
    ma5           = mas[cfg["ma_short"]].iloc[-1]
    ma20          = mas[cfg["ma_mid"]].iloc[-1]
    ma60          = mas[cfg["ma_long"]].iloc[-1] if len(close) >= cfg["ma_long"] else None

    score   = 0
    details = {}

    # ── Step-1：量价结构（最高 20 分 × 振幅权重）───────────────────────────────
    amplitude_subscore = 0
    # 振幅横盘（最高 10 分）
    if amplitude_20d < 0.10:
        amplitude_subscore += 10
        details["amplitude"] = f"✅ 极度横盘，20日振幅 {amplitude_20d:.2%}（< 10%），+10分"
    elif amplitude_20d < 0.15:
        amplitude_subscore += 6
        details["amplitude"] = f"✅ 横盘整理，20日振幅 {amplitude_20d:.2%}（< 15%），+6分"
    elif amplitude_20d < 0.25:
        amplitude_subscore += 2
        details["amplitude"] = f"🔶 振幅尚可 {amplitude_20d:.2%}（< 25%），+2分"
    else:
        details["amplitude"] = f"❌ 振幅过大 {amplitude_20d:.2%}（≥ 25%）"

    # 成交量异动（最高 10 分）
    vol_ma20 = recent_20["vol"].mean()
    vol_ratio = latest["vol"] / vol_ma20 if vol_ma20 > 0 else 0
    if 1.5 <= vol_ratio <= 3.0 and latest["pct_chg"] > 0:
        amplitude_subscore += 10
        details["volume"] = f"✅ 温和放量上涨，量比 {vol_ratio:.2f}，+10分"
    elif vol_ratio > 3.0 and latest["pct_chg"] > 0:
        amplitude_subscore += 6
        details["volume"] = f"🔶 大幅放量上涨，量比 {vol_ratio:.2f}（注意追高风险），+6分"
    elif vol_ratio < 0.5 and amplitude_20d < 0.15:
        amplitude_subscore += 4
        details["volume"] = f"✅ 缩量横盘（底部吸筹特征），量比 {vol_ratio:.2f}，+4分"
    else:
        details["volume"] = f"量比 {vol_ratio:.2f}（无明显异动）"
    
    # 应用振幅权重
    amplitude_subscore = int(amplitude_subscore * weights["amplitude"])
    score += amplitude_subscore

    # ── Step-2：技术指标（最高 30 分 × 技术权重）───────────────────────────────
    tech_subscore = 0
    
    # MACD（最高 15 分）
    macd_score = 0
    macd_detail_parts = []
    # 金叉：DIF 从下穿 DEA
    if prev_dif <= prev_dea and latest_dif > latest_dea:
        macd_score += 12
        macd_detail_parts.append("MACD 金叉（+12分）")
    elif latest_dif > latest_dea:
        macd_score += 6
        macd_detail_parts.append("MACD DIF > DEA（+6分）")
    # MACD 柱状线转正
    if prev_macd < 0 and latest_macd >= 0:
        macd_score += 3
        macd_detail_parts.append("MACD 柱状线转正（+3分）")
    macd_score = min(macd_score, 15)
    tech_subscore += macd_score
    details["macd"] = ("✅ " if macd_score >= 6 else "❌ ") + \
                      (", ".join(macd_detail_parts) if macd_detail_parts
                       else f"MACD 未金叉，DIF={latest_dif:.4f} DEA={latest_dea:.4f}")

    # RSI（最高 10 分）
    rsi_score = 0
    if not np.isnan(latest_rsi):
        if cfg["rsi_oversold"] <= latest_rsi <= 55:
            rsi_score = 10
            details["rsi"] = f"✅ RSI={latest_rsi:.1f}，从超卖区回升（+10分）"
        elif 55 < latest_rsi <= 65:
            rsi_score = 6
            details["rsi"] = f"✅ RSI={latest_rsi:.1f}，健康强势区间（+6分）"
        elif latest_rsi < cfg["rsi_oversold"]:
            rsi_score = 3
            details["rsi"] = f"🔶 RSI={latest_rsi:.1f}，超卖（仍需确认底部）（+3分）"
        else:
            details["rsi"] = f"❌ RSI={latest_rsi:.1f}（超买区，不加分）"
    else:
        details["rsi"] = "⚠️ RSI 数据不足"
    tech_subscore += rsi_score

    # 均线多头排列（最高 5 分）
    close_now = float(latest["close"])
    if not np.isnan(ma5) and not np.isnan(ma20):
        if close_now > ma5 > ma20:
            ma_score = 3
            details["ma"] = f"✅ 均线多头（价>{ma5:.2f}>{ma20:.2f}）（+3分）"
            if ma60 is not None and not np.isnan(ma60) and ma20 > ma60:
                ma_score = 5
                details["ma"] = f"✅ 完美多头排列（{close_now:.2f}>{ma5:.2f}>{ma20:.2f}>{ma60:.2f}）（+5分）"
        elif close_now > ma20:
            ma_score = 1
            details["ma"] = f"🔶 价格站上 MA20（+1分）"
        else:
            ma_score = 0
            details["ma"] = f"❌ 价格低于 MA20，空头排列"
    else:
        ma_score = 0
        details["ma"] = "⚠️ 均线数据不足"
    tech_subscore += ma_score
    
    # 应用技术权重
    tech_subscore = int(tech_subscore * weights["technical"])
    score += tech_subscore

    # ── Step-3：筹码集中度（最高 30 分 × 筹码权重）─────────────────────────────
    holder_subscore = 0
    holder_chg = 0  # 新增：股东户数变化百分比
    holder_current = 0  # 新增：当前股东户数
    holder_prev = 0  # 新增：上期股东户数
    df_holder = _load_holder(conn, ts_code)
    if len(df_holder) >= 2:
        n1 = df_holder.iloc[0]["holder_num"]
        n2 = df_holder.iloc[1]["holder_num"]
        holder_current = n1
        holder_prev = n2
        if n2 and n2 > 0 and not pd.isna(n1):
            chg = (n1 - n2) / n2
            holder_chg = chg  # 保存股东户数变化
            if chg < -0.10:
                holder_subscore += 30
                details["holder"] = f"✅ 股东户数大幅减少 {chg:.2%}（>10%），筹码高度集中，+30分"
            elif chg < -0.05:
                holder_subscore += 18
                details["holder"] = f"✅ 股东户数减少 {chg:.2%}（5~10%），筹码集中，+18分"
            elif chg < -0.02:
                holder_subscore += 8
                details["holder"] = f"🔶 股东户数小幅减少 {chg:.2%}（2~5%），+8分"
            elif chg < 0:
                holder_subscore += 3
                details["holder"] = f"🔶 股东户数微降 {chg:.2%}，+3分"
            else:
                details["holder"] = f"❌ 股东户数增加 {chg:.2%}，筹码分散"
        else:
            details["holder"] = "⚠️ 股东户数数据异常"
    else:
        details["holder"] = "⚠️ 筹码数据缺失（stk_holdernumber 不足2期）"
    
    # 应用筹码权重
    holder_subscore = int(holder_subscore * weights["holder"])
    score += holder_subscore

    # ── Step-4：主力资金（最高 20 分 × 资金权重）───────────────────────────────
    money_subscore = 0
    main_money = 0  # 新增：主力资金净流入（万元）
    main_money_ratio = 0  # 新增：主力资金占比
    total_volume = 0  # 新增：总成交额（万元）
    df_money = _load_moneyflow(conn, ts_code)
    if not df_money.empty:
        m = df_money.iloc[0]
        needed = ["buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount"]
        if all(c in m.index for c in needed):
            net_main = ((m["buy_elg_amount"] + m["buy_lg_amount"])
                        - (m["sell_elg_amount"] + m["sell_lg_amount"]))
            main_money = net_main  # 保存主力资金净流入
            # 计算主力资金占比
            total_main = m["buy_elg_amount"] + m["sell_elg_amount"] + m["buy_lg_amount"] + m["sell_lg_amount"]
            total_volume = m.get("amount", total_main)  # 总成交额
            if total_volume > 0:
                main_money_ratio = (total_main / total_volume) * 100
            # 正向背离（跌但主力流入）- 使用背离权重
            if net_main > 0 and latest["pct_chg"] < 0:
                base_score = 20
                # 背离加分使用背离权重
                money_subscore += int(base_score * weights["divergence"])
                details["moneyflow"] = (
                    f"✅ 正向背离：股价跌 {latest['pct_chg']:.2f}%，"
                    f"主力净流入 {net_main:.0f} 万元（最高加分），+{int(base_score * weights['divergence'])}分"
                )
            elif net_main > 0:
                base_score = 10
                money_subscore += int(base_score * weights["moneyflow"])
                details["moneyflow"] = f"✅ 主力净流入 {net_main:.0f} 万元，+{int(base_score * weights['moneyflow'])}分"
            elif net_main < -1000:
                details["moneyflow"] = f"❌ 主力净流出 {abs(net_main):.0f} 万元"
            else:
                base_score = 2
                money_subscore += int(base_score * weights["moneyflow"])
                details["moneyflow"] = f"🔶 主力资金中性（{net_main:.0f} 万元），+{int(base_score * weights['moneyflow'])}分"
        else:
            details["moneyflow"] = "⚠️ 资金流字段不完整"
    else:
            details["moneyflow"] = "⚠️ 无资金流向数据"
    score += money_subscore

    # ── Step-5：行业催化评分（最高 30 分）─────────────────────────────────────
    catalyst_score = _get_catalyst_score(industry, conn)
    score += catalyst_score
    details["catalyst"] = f"🔶 行业催化得分: {catalyst_score}分（行业: {industry}）"

    # ── 新增：北向资金5日净流入数据 ──────────────────────────────────────
    hsgt_5d = _load_hsgt(conn, ts_code)
    
    return {
        "ts_code":        ts_code,
        "name":           name,
        "industry":       industry,
        "score":          score,
        "catalyst_score": catalyst_score,
        "trade_date":     latest["trade_date"],
        "close":          round(float(latest["close"]), 2),
        "pct_chg":        round(float(latest["pct_chg"]), 2),
        "amount_w":       round(float(latest["amount"]) / 100, 2),  # 转换为万元显示
        "amplitude_20d":  round(float(amplitude_20d), 4),
        "vol_ratio":      round(float(vol_ratio), 2),
        "rsi":            round(float(latest_rsi), 2) if not np.isnan(latest_rsi) else None,
        "macd_dif":       round(float(latest_dif), 6),
        "macd_dea":       round(float(latest_dea), 6),
        "ma5":            round(float(ma5), 2) if not np.isnan(ma5) else None,
        "ma20":           round(float(ma20), 2) if not np.isnan(ma20) else None,
        "details":        details,
        "filter_time":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_regime":  weights.get("regime", "neutral"),
        # 新增：主力资金和股东户数具体数值
        "main_money":     round(float(main_money), 2) if main_money else 0,
        "main_money_ratio": round(float(main_money_ratio), 2) if main_money_ratio else 0,
        "total_volume":   round(float(total_volume), 2) if total_volume else 0,
        "holder_chg":     round(float(holder_chg), 4) if holder_chg else 0,
        "holder_current": int(holder_current) if holder_current else 0,
        "holder_prev":    int(holder_prev) if holder_prev else 0,
        # 新增：北向资金5日净流入
        "hsgt_5d":       round(float(hsgt_5d), 2) if hsgt_5d else 0,
    }


# =============================================================================
# 主过滤引擎
# =============================================================================

class FilterEngine:
    """
    漏斗第一层：Python / SQL 硬过滤引擎。
    ⚠️ 此类绝对不调用 Ollama 或任何 AI 推理接口。
    """

    def __init__(self, db_path: str = DB_PATH, cfg: dict = None):
        self.db_path = db_path
        self.cfg     = cfg or FILTER_CONFIG
        os.makedirs(self.cfg["output_dir"], exist_ok=True)
        log.info("FilterEngine 初始化完成 | DB: %s", db_path)

    def run_filter(self, top_n: int = None) -> Tuple[bool, List[Dict]]:
        """
        执行完整的一层过滤流程。

        Returns:
            (success: bool, results: list[dict])
            results 已按 score 降序排列，最多返回 top_n 只股票。
        """
        top_n = top_n or self.cfg["top_n"]
        log.info("=" * 60)
        log.info("🚀 StockAI_Funnel 第一层硬过滤启动")
        log.info("=" * 60)

        conn = None
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA temp_store=MEMORY;")

            # 1. 加载股票列表
            stock_df = _load_stock_list(conn)
            total    = len(stock_df)
            log.info("📋 全市场股票数: %d", total)

            # 2. 获取市场状态，确定动态权重
            market_regime = get_market_regime(conn)
            weights = DYNAMIC_WEIGHTS[market_regime]["weights"].copy()
            weights["regime"] = market_regime
            log.info(f"📊 当前市场状态: {DYNAMIC_WEIGHTS[market_regime]['name']} ({DYNAMIC_WEIGHTS[market_regime]['description']})")
            log.info(f"⚖️  应用权重: 资金{weights['moneyflow']}× 振幅{weights['amplitude']}× 筹码{weights['holder']}× 技术{weights['technical']}× 背离{weights['divergence']}×")

            # 3. 逐只评分
            results   = []
            veto_cnt  = 0
            error_cnt = 0
            start_t   = time.time()

            for i, row in enumerate(stock_df.itertuples(index=False), 1):
                try:
                    r = score_one(
                        ts_code=row.ts_code,
                        name=row.name,
                        industry=getattr(row, "industry", ""),
                        conn=conn,
                        cfg=self.cfg,
                        weights=weights,
                        row=row,
                    )
                    if r is None:
                        veto_cnt += 1
                    else:
                        results.append(r)
                        error_cnt = 0   # 成功后重置熔断计数

                except Exception as e:
                    error_cnt += 1
                    log.debug("评分失败 %s: %s", row.ts_code, e)
                    if error_cnt >= 15:
                        log.error("⚡ 连续 %d 次错误，触发熔断！扫描中止。", error_cnt)
                        break

                if i % 500 == 0 or i == total:
                    elapsed = time.time() - start_t
                    log.info(
                        "进度 %d/%d (%.1f%%) | 候选: %d | 否决: %d | 耗时 %.0fs",
                        i, total, i / total * 100, len(results), veto_cnt, elapsed
                    )

            if not results:
                log.warning("❌ 硬过滤后无任何候选股")
                return False, []

            # 4. 排序，取 Top-N
            results.sort(key=lambda x: x["score"], reverse=True)
            top_results = results[:top_n]

            log.info(
                "✅ 过滤完成 | 全市场: %d | 候选池: %d | 输出 Top-%d | 最高分: %d | 最低分: %d",
                total, len(results), top_n,
                top_results[0]["score"] if top_results else 0,
                top_results[-1]["score"] if top_results else 0,
            )

            # 5. 持久化输出
            self._save_result(top_results)

            return True, top_results

        except Exception as e:
            log.error("❌ 过滤引擎异常: %s", e, exc_info=True)
            return False, []
        finally:
            if conn:
                conn.close()

    def _save_result(self, results: List[Dict]) -> str:
        """将结果保存为 JSON 文件，返回文件路径。"""
        filename    = f"filter_top{len(results)}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        output_path = os.path.join(self.cfg["output_dir"], filename)

        # 同时写一份固定名称的"最新结果"，方便下游直接读取
        latest_path = os.path.join(self.cfg["output_dir"], "latest.json")

        payload = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_candidates": len(results),
            "stocks": results,
        }

        def _json_default(o):
            import numpy as np
            if isinstance(o, np.integer):
                return int(o)
            elif isinstance(o, np.floating):
                return float(o)
            elif isinstance(o, np.ndarray):
                return o.tolist()
            raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")

        for path in [output_path, latest_path]:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=_json_default)

        log.info("💾 过滤结果已保存 → %s", output_path)
        return output_path

    @staticmethod
    def load_latest(output_dir: str = None) -> List[Dict]:
        """
        加载最新一次过滤结果（供第二层 AI 分析器调用）。
        若文件不存在则返回空列表。
        """
        if output_dir is None:
            output_dir = FILTER_CONFIG["output_dir"]
        path = os.path.join(output_dir, "latest.json")
        if not os.path.exists(path):
            log.warning("未找到 latest.json，请先运行 FilterEngine.run_filter()")
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        stocks = data.get("stocks", [])
        log.info("📂 加载过滤结果：%d 只候选股（生成时间: %s）",
                 len(stocks), data.get("generated_at", "未知"))
        return stocks


# =============================================================================
# 独立运行入口
# =============================================================================
if __name__ == "__main__":
    engine = FilterEngine()
    success, results = engine.run_filter()

    if success:
        print(f"\n{'='*60}")
        print(f"✅ 第一层过滤完成：选出 {len(results)} 只候选股")
        print(f"{'='*60}")
        print(f"{'排名':<4} {'代码':<12} {'名称':<10} {'行业':<12} {'分数':<6} {'RSI':<8} {'量比':<7} {'收盘'}")
        print("-" * 75)
        for i, s in enumerate(results, 1):
            print(f"{i:<4} {s['ts_code']:<12} {s['name']:<10} "
                  f"{s['industry']:<12} {s['score']:<6} "
                  f"{str(s.get('rsi', 'N/A')):<8} {s['vol_ratio']:<7} {s['close']}")
    else:
        print("❌ 过滤失败，请检查数据库连接和日志")
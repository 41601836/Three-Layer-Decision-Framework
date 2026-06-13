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
# 配置常量（主力资金提前嗅探 · 横盘吸筹识别器）
# =============================================================================
FILTER_CONFIG = {
    # 数据窗口
    "lookback_days":           60,     # 向前取多少个自然日的行情
    "min_data_points":         20,     # 每只股票至少需要 N 日数据
    # 硬过滤阈值（横盘吸筹策略）
    "min_amount":              5e7,    # 成交额最低 5000 万元
    "min_amount_tushare":      50000,  # 对应 tushare daily_prices.amount（千元）
    "max_amplitude_veto":      0.50,   # 振幅 > 50% 直接剔除
    "max_amplitude_risk":      0.30,   # 振幅 > 30% 风险扣分（-5分）
    "min_circulating_mv_billion": 10.0,  # 流通市值最低 10 亿元（过滤微盘股）
    # 信号等级阈值
    "strong_signal":           30,     # 强信号阈值（主力+筹码双核心）
    "medium_signal":           15,     # 中信号阈值（单核心满足）
    # 技术指标参数
    "macd_fast":               12,
    "macd_slow":               26,
    "macd_signal":             9,
    "rsi_period":              14,
    "rsi_oversold":            30,
    "rsi_overbought":          75,
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
    主力资金提前嗅探 · 横盘吸筹识别器
    
    策略哲学：在 A 股市场中，主力资金的建仓行为通常会在盘面上留下两种痕迹：
    1. 资金痕迹：大单（特大单+大单）持续净买入，散户（小单）却在卖出
    2. 筹码痕迹：股东户数持续减少，意味着散户离场、筹码向少数人集中
    
    当这两种痕迹同时出现，且股价尚未大涨时，说明主力正在"横盘吸筹"——这是拉升前夜最重要的信号。
    
    返回 None 表示被一票否决（不进入候选池）。
    返回 dict 包含：ts_code / name / industry / score / signal_level / details
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

    # ── Step-0：硬性过滤规则（直接排除）───────────────────────────────────────
    # ST 股
    if "ST" in name.upper() or "st" in name.lower():
        return None
    # 今日跌停
    if latest["pct_chg"] < -9.5:
        return None
    # 成交额不足（日成交额 < 5000万）
    if latest["amount"] < cfg["min_amount_tushare"]:
        return None
    # 极端振幅（20日振幅 > 50%）
    h20, l20 = recent_20["high"].max(), recent_20["low"].min()
    amplitude_20d = (h20 - l20) / l20 if l20 > 0 else 1.0
    if amplitude_20d > cfg["max_amplitude_veto"]:
        return None
    # 微盘股过滤：流通市值 < 10亿
    circ_mv = getattr(row, "circ_mv", None)
    if circ_mv is not None and not (isinstance(circ_mv, float) and circ_mv != circ_mv):
        min_mv_wan = cfg.get("min_circulating_mv_billion", 10.0) * 10000
        if float(circ_mv) < min_mv_wan:
            return None
    
    # ── Step-0.5：三重流出否决机制（一票否决）─────────────────────────────────
    # 当以下三个条件同时成立时，信号总分直接清零
    veto_reasons = []
    
    # 条件1：主力资金净流出
    df_money = _load_moneyflow(conn, ts_code)
    main_outflow = False
    if not df_money.empty:
        m = df_money.iloc[0]
        needed = ["buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount"]
        if all(c in m.index for c in needed):
            net_main = ((m["buy_elg_amount"] + m["buy_lg_amount"])
                        - (m["sell_elg_amount"] + m["sell_lg_amount"]))
            if net_main < 0:
                main_outflow = True
                veto_reasons.append(f"主力资金净流出 {net_main:.0f}万元")
    
    # 条件2：融资余额下降
    margin_down = False
    try:
        cursor = conn.execute("""
            SELECT rzye FROM margin_detail 
            WHERE ts_code = ? ORDER BY trade_date DESC LIMIT 2
        """, (ts_code,))
        rows = cursor.fetchall()
        if len(rows) >= 2 and rows[0][0] is not None and rows[1][0] is not None:
            if rows[0][0] < rows[1][0]:
                margin_down = True
                veto_reasons.append(f"融资余额下降")
    except:
        pass
    
    # 条件3：北向资金流出
    north_outflow = False
    try:
        cursor = conn.execute("""
            SELECT north_money FROM hsgt_moneyflow 
            ORDER BY trade_date DESC LIMIT 6
        """)
        rows = cursor.fetchall()
        if len(rows) >= 6:
            recent_3d = sum(r[0] for r in rows[:3] if r[0] is not None) / 3
            prev_3d = sum(r[0] for r in rows[3:6] if r[0] is not None) / 3
            if recent_3d < prev_3d:
                north_outflow = True
                veto_reasons.append(f"北向资金流出")
    except:
        pass
    
    # 三重流出同时成立，一票否决
    if main_outflow and margin_down and north_outflow:
        log.debug(f"{ts_code} {name} 三重流出否决: {', '.join(veto_reasons)}")
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
    
    # ── 核心因子1：主力资金净流入（+15分）─────────────────────────────────────
    money_score = 0
    df_money = _load_moneyflow(conn, ts_code)
    if not df_money.empty:
        m = df_money.iloc[0]
        needed = ["buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount"]
        if all(c in m.index for c in needed):
            net_main = ((m["buy_elg_amount"] + m["buy_lg_amount"])
                        - (m["sell_elg_amount"] + m["sell_lg_amount"]))
            if net_main > 0:
                money_score = 15
                details["moneyflow"] = f"✅ 主力资金净流入 {net_main:.0f} 万元，+15分"
            else:
                details["moneyflow"] = f"❌ 主力资金净流出 {abs(net_main):.0f} 万元，不得分"
        else:
            # 数据不完整，尝试用其他字段
            if "net_amount" in m.index and m["net_amount"] > 0:
                money_score = 15
                details["moneyflow"] = f"✅ 主力资金净流入 {m['net_amount']:.0f} 万元（使用替代字段），+15分"
            else:
                details["moneyflow"] = "⚠️ 资金流字段不完整"
    else:
        # 资金流数据缺失，使用成交量和涨幅作为替代指标
        vol_ma20 = recent_20["vol"].mean()
        vol_ratio = latest["vol"] / vol_ma20 if vol_ma20 > 0 else 0
        if vol_ratio >= 1.5 and latest["pct_chg"] > 0:
            money_score = 10
            details["moneyflow"] = f"✅ 放量上涨替代：量比{vol_ratio:.2f}，涨幅{latest['pct_chg']:.2f}%，+10分"
        elif vol_ratio >= 1.2 and latest["pct_chg"] > 0:
            money_score = 5
            details["moneyflow"] = f"🔶 温和放量上涨：量比{vol_ratio:.2f}，+5分"
        else:
            details["moneyflow"] = "⚠️ 无资金流向数据，使用成交量替代"
    score += money_score
    
    # ── 核心因子2：股东户数连续下降（+15分）───────────────────────────────────
    holder_score = 0
    holder_decline_count = 0
    df_holder = _load_holder(conn, ts_code)
    if len(df_holder) >= 2:
        n1 = df_holder.iloc[0]["holder_num"]
        n2 = df_holder.iloc[1]["holder_num"]
        if n2 and n2 > 0 and not pd.isna(n1):
            chg = (n1 - n2) / n2
            if chg < 0:
                holder_score = 15
                holder_decline_count = 1
                if len(df_holder) >= 3:
                    n3 = df_holder.iloc[2]["holder_num"]
                    if n3 and n3 > 0 and not pd.isna(n2):
                        chg2 = (n2 - n3) / n3
                        if chg2 < 0:
                            holder_decline_count = 2
                            details["holder"] = f"✅ 股东户数连续2期下降（{chg2:.2%} → {chg:.2%}），筹码持续集中，+15分"
                        else:
                            details["holder"] = f"✅ 股东户数下降 {chg:.2%}，筹码集中，+15分"
                else:
                    details["holder"] = f"✅ 股东户数下降 {chg:.2%}，筹码集中，+15分"
            else:
                details["holder"] = f"❌ 股东户数增加 {chg:.2%}，筹码分散，不得分"
        else:
            # 股东户数数据异常，使用其他指标替代
            holder_score = 5
            details["holder"] = "⚠️ 股东户数数据异常，使用替代指标，+5分"
    else:
        # 股东户数数据缺失，使用集中度指标替代
        holder_score = 5
        details["holder"] = "⚠️ 筹码数据缺失，使用替代指标，+5分"
    score += holder_score
    
    # ── 增强因子：三日背离（+10分，不计入强信号阈值）─────────────────────────
    divergence_score = 0
    if not df_money.empty and len(df) >= 3:
        recent_3d = df.tail(3)
        m = df_money.iloc[0]
        needed = ["buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount"]
        if all(c in m.index for c in needed):
            # 检查连续3日主力净流入
            moneyflow_rows = df_money.head(3)
            if len(moneyflow_rows) >= 3:
                total_net = 0
                for i in range(min(3, len(moneyflow_rows))):
                    row = moneyflow_rows.iloc[i]
                    if all(c in row.index for c in needed):
                        total_net += ((row["buy_elg_amount"] + row["buy_lg_amount"])
                                    - (row["sell_elg_amount"] + row["sell_lg_amount"]))
                
                # 累计涨幅 < 0（股价下跌）
                pct_chg_3d = recent_3d["pct_chg"].sum() if len(recent_3d) >= 3 else 0
                
                if total_net > 0 and pct_chg_3d < 0:
                    divergence_score = 10
                    details["divergence"] = f"✅ 三日背离：主力持续流入但股价累计跌{pct_chg_3d:.2f}%，逆势吸筹，+10分（增强因子）"
    if divergence_score == 0:
        details["divergence"] = "三日背离条件未满足"
    
    # ── 风险扣除：振幅异常（-5分）───────────────────────────────────────────
    amplitude_penalty = 0
    # 检查大盘振幅
    sh_amplitude = 0.0
    try:
        cursor = conn.execute("""
            SELECT high, low FROM daily_index 
            WHERE ts_code = '000001.SH' ORDER BY trade_date DESC LIMIT 20
        """)
        sh_rows = cursor.fetchall()
        if len(sh_rows) >= 2:
            sh_high = max(r[0] for r in sh_rows if r[0] is not None)
            sh_low = min(r[1] for r in sh_rows if r[1] is not None)
            if sh_low > 0:
                sh_amplitude = (sh_high - sh_low) / sh_low
    except:
        pass
    
    max_amplitude_risk = cfg.get("max_amplitude_risk", 0.30)
    if amplitude_20d > max_amplitude_risk and sh_amplitude < 0.10:
        amplitude_penalty = -5
        details["amplitude_risk"] = f"❌ 振幅异常：个股20日振幅{amplitude_20d:.2%}，大盘{sh_amplitude:.2%}，存在操纵嫌疑，-5分"
    else:
        details["amplitude_risk"] = f"振幅风险评估正常（个股{amplitude_20d:.2%}，大盘{sh_amplitude:.2%}）"
        
    # ── 补充维度1：量价结构（+20分）─────────────────────────────────────────
    vp_score = 0
    # 振幅横盘 (10分): 20日振幅 < 15%
    if amplitude_20d < 0.15:
        vp_score += 10
        details["amplitude"] = f"✅ 横盘 振幅 {amplitude_20d:.2%} < 15%，+10分"
    else:
        details["amplitude"] = f"❌ 振幅 {amplitude_20d:.2%} >= 15%，0分"
        
    # 量比异动 (10分): 今日量比 >= 1.5
    vol_ma20 = recent_20["vol"].mean()
    vol_ratio = latest["vol"] / vol_ma20 if vol_ma20 > 0 else 0
    if vol_ratio >= 1.5:
        vp_score += 10
        details["vol_ratio"] = f"✅ 量比异动 {vol_ratio:.2f} >= 1.5，+10分"
    else:
        details["vol_ratio"] = f"❌ 量比 {vol_ratio:.2f} < 1.5，0分"
    score += vp_score
    
    # ── 补充维度2：技术指标（+30分）─────────────────────────────────────────
    tech_score = 0
    # MACD金叉 (10分): DIF > DEA 且近期发生交叉
    if latest_dif > latest_dea and prev_dif <= prev_dea:
        tech_score += 10
        details["macd"] = "✅ MACD 金叉，+10分"
    elif latest_dif > latest_dea:
        tech_score += 5
        details["macd"] = "🔶 MACD 多头，+5分"
    else:
        details["macd"] = "❌ MACD 空头，0分"
        
    # RSI区间 (10分): 50 < RSI < 80 强势区
    if 50 < latest_rsi < 80:
        tech_score += 10
        details["rsi"] = f"✅ RSI 强势区 ({latest_rsi:.1f})，+10分"
    else:
        details["rsi"] = f"❌ RSI 非强势区 ({latest_rsi:.1f})，0分"
        
    # 均线多头 (10分): 5日 > 20日 > 60日
    if ma60 is not None and ma5 > ma20 > ma60:
        tech_score += 10
        details["ma"] = "✅ 均线多头排列，+10分"
    elif ma20 is not None and ma5 > ma20:
        tech_score += 5
        details["ma"] = "🔶 短期均线多头，+5分"
    else:
        details["ma"] = "❌ 均线非多头，0分"
    score += tech_score
    
    # ── 计算最终得分 ────────────────────────────────────────────────────────
    final_score = score + divergence_score + amplitude_penalty
    
    # ── 信号等级判定 ────────────────────────────────────────────────────────
    strong_signal = cfg.get("strong_signal", 30)
    medium_signal = cfg.get("medium_signal", 15)
    
    if final_score >= strong_signal:
        signal_level = "强信号"
        signal_desc = "主力资金+筹码双核心全满，重点关注，可建仓"
    elif final_score >= medium_signal:
        signal_level = "中信号"
        signal_desc = "单核心满足，加入观察池，等待另一因子确认"
    else:
        signal_level = "弱信号"
        signal_desc = "信号不足，不推送，不参与"
    
    # 弱信号直接过滤
    if final_score < medium_signal:
        return None

    return {
        "ts_code":           ts_code,
        "name":              name,
        "industry":          industry,
        "score":             final_score,
        "core_score":        score,
        "divergence_score":  divergence_score,
        "amplitude_penalty": amplitude_penalty,
        "signal_level":      signal_level,
        "signal_desc":       signal_desc,
        "trade_date":        latest["trade_date"],
        "close":             round(float(latest["close"]), 2),
        "pct_chg":           round(float(latest["pct_chg"]), 2),
        "amount_w":          round(float(latest["amount"]) / 100, 2),
        "amplitude_20d":     round(float(amplitude_20d), 4),
        "details":           details,
        "filter_time":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_regime":     weights.get("regime", "neutral"),
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
        print(f"{'排名':<4} {'代码':<12} {'名称':<10} {'行业':<12} {'分数':<6} {'收盘'}")
        print("-" * 75)
        for i, s in enumerate(results, 1):
            print(f"{i:<4} {s['ts_code']:<12} {s['name']:<10} "
                  f"{s['industry']:<12} {s['score']:<6} {s['close']}")
    else:
        print("❌ 过滤失败，请检查数据库连接和日志")
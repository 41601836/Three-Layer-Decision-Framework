# -*- coding: utf-8 -*-
"""
scanner.py —— StockAI v2.1 全市场扫描引擎
=====================================================================
评分体系（满分 100）：
  - 一票否决  : ST股 / 跌停 → 0分
  - 量价异动  : 25分（横盘10 + 吸筹形态15）
  - 筹码集中  : 25分（股东户数下降）
  - 主力背离  : 20分（正向背离）
  - 产业催化  : 30分（AI评估，Python端记0，由 ai_report.py 补充）

输出：
  - 返回 DataFrame，字段包含 ts_code/name/score/grade/details
  - grade: S(≥85) / A(70-84) / B(40-69) / C(<40)
"""

import os
import sys
import sqlite3
import logging
import time
from datetime import datetime

import pandas as pd
import tushare as ts

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")
sys.path.insert(0, ROOT_DIR)

log = logging.getLogger(__name__)

# 连续API错误熔断阈值
CIRCUIT_BREAKER_LIMIT = 10


def _get_pro():
    try:
        from scripts.tokens import TOKEN
    except ImportError:
        from tokens import TOKEN
    ts.set_token(TOKEN)
    return ts.pro_api()


pro = _get_pro()


# =============================================================================
# 交易日判断
# =============================================================================
_trade_cal_cache: dict = {}

def is_trade_day(date_str: str = None) -> bool:
    """判断指定日期（YYYYMMDD）是否为A股交易日，默认今天。"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    global _trade_cal_cache
    if not _trade_cal_cache:
        try:
            df = pro.trade_cal(exchange="SSE",
                               start_date=datetime.now().strftime("%Y0101"),
                               end_date=datetime.now().strftime("%Y1231"),
                               fields="cal_date,is_open")
            _trade_cal_cache = dict(zip(df["cal_date"], df["is_open"]))
            log.info("交易日历已加载 %d 条", len(_trade_cal_cache))
        except Exception as e:
            log.warning("交易日历加载失败，默认非周末视为交易日: %s", e)
            # 降级：非周末视为交易日
            dt = datetime.strptime(date_str, "%Y%m%d")
            return dt.weekday() < 5

    return bool(_trade_cal_cache.get(date_str, 0))


# =============================================================================
# 单只股票评分
# =============================================================================
def score_one(ts_code: str, conn: sqlite3.Connection,
              name: str = "", industry: str = "") -> dict:
    """
    对单只股票执行 v2.1 评分（Python维度，满分70，催化剂由AI补充）。
    返回 dict 包含: ts_code, name, industry, score, veto, details, data_json
    """
    result = {
        "ts_code":  ts_code,
        "name":     name,
        "industry": industry,
        "score":    0,
        "veto":     None,
        "grade":    "C",
        "details":  {},
        "data_json": {}
    }

    # ── 获取日线数据 ─────────────────────────────────────────────────────────
    try:
        df_daily = pd.read_sql(
            """SELECT trade_date, open, high, low, close, pct_chg, vol, amount
               FROM   daily_prices
               WHERE  ts_code = ?
               ORDER  BY trade_date DESC LIMIT 25""",
            conn, params=(ts_code,)
        )
    except Exception as e:
        log.debug("日线读取失败 %s: %s", ts_code, e)
        return result

    if df_daily.empty or len(df_daily) < 5:
        return result

    latest = df_daily.iloc[0]

    # ── Step 0：风险一票否决 ─────────────────────────────────────────────────
    if "ST" in name.upper():
        result["veto"] = "⛔ ST股，停止分析"
        return result

    if latest["pct_chg"] < -9.5:
        result["veto"] = "⛔ 今日跌停，停止分析"
        return result
        
    if latest["amount"] < 50000:
        result["veto"] = "⛔ 成交额低于5000万"
        return result

    # 基础分保底 40 分，最高 80 分
    score   = 40
    details = {}

    # ── Step 1：量价异动 (最高 10分) ──────────────────────────────────────────────
    recent_20 = df_daily.head(20)
    h_max = recent_20["high"].max()
    l_min = recent_20["low"].min()
    amplitude = (h_max - l_min) / l_min if l_min > 0 else 1.0

    if amplitude < 0.15:
        score += 5
        details["amplitude"] = "✅ 横盘 振幅 {:.2%} < 15%，+5分".format(amplitude)
    elif amplitude < 0.25:
        score += 2
        details["amplitude"] = "🔶 振幅尚可 {:.2%} < 25%，+2分".format(amplitude)
    else:
        details["amplitude"] = "❌ 振幅过大 {:.2%} ≥ 25%".format(amplitude)

    vol_ma20 = recent_20["vol"].mean()
    kline_hit = False
    
    # 放宽技术指标：趋势向上或底部放量异动
    if latest["close"] > df_daily.iloc[min(len(df_daily)-1, 19)]["close"]: # 20日趋势向上
        kline_hit = True
        score += 5
        details["kline"] = "✅ 20日趋势向上，+5分"
    else:
        for _, row in df_daily.head(3).iterrows():
            if vol_ma20 > 0 and row["vol"] > vol_ma20 * 1.5 and row["pct_chg"] > 0:
                kline_hit = True
                score += 5
                details["kline"] = "✅ 底部放量异动，+5分"
                break
                
        if not kline_hit:
            details["kline"] = "❌ 未检测到趋势向上或异动"

    # ── Step 2：筹码集中度 (最高 15分) ────────────────────────────────────────────
    try:
        df_holder = pd.read_sql(
            """SELECT end_date, holder_num FROM stk_holdernumber
               WHERE ts_code = ? ORDER BY end_date DESC LIMIT 2""",
            conn, params=(ts_code,)
        )
    except Exception:
        df_holder = pd.DataFrame()

    if len(df_holder) >= 2:
        num1 = df_holder.iloc[0]["holder_num"]
        num2 = df_holder.iloc[1]["holder_num"]
        if num2 and num2 > 0:
            chg = (num1 - num2) / num2
            if chg < -0.10:
                score += 15
                details["holder"] = "✅ 股东户数减少 {:.2%}（>10%），+15分".format(chg)
            elif chg < -0.05:
                score += 8
                details["holder"] = "✅ 股东户数减少 {:.2%}（5~10%），+8分".format(chg)
            elif chg < 0:
                score += 3
                details["holder"] = "🔶 股东户数小幅减少 {:.2%}，+3分".format(chg)
            else:
                details["holder"] = "❌ 股东户数增加 {:.2%}，不达标".format(chg)
        else:
            details["holder"] = "⚠️ 户数数据异常"
    else:
        details["holder"] = "⚠️ 筹码数据缺失（stk_holdernumber不足2期）"

    # ── Step 3：主力行为背离 (最高 15分) ──────────────────────────────────────────
    try:
        df_money = pd.read_sql(
            """SELECT trade_date, buy_elg_amount, sell_elg_amount,
                      buy_lg_amount, sell_lg_amount, net_mf_amount
               FROM   moneyflow
               WHERE  ts_code = ?
               ORDER  BY trade_date DESC LIMIT 1""",
            conn, params=(ts_code,)
        )
    except Exception:
        df_money = pd.DataFrame()

    if not df_money.empty:
        m = df_money.iloc[0]
        needed = ["buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount"]
        if all(c in m.index for c in needed):
            net = ((m["buy_elg_amount"] + m["buy_lg_amount"])
                   - (m["sell_elg_amount"] + m["sell_lg_amount"]))
            if net > 0 and latest["pct_chg"] < 0:
                score += 15
                details["divergence"] = (
                    "✅ 正向背离：股价跌 {:.2f}% 但主力净流入 {:.0f} 万元，+15分".format(
                        latest["pct_chg"], net)
                )
            elif net > 0:
                score += 5
                details["divergence"] = "🔶 主力净流入 {:.0f} 万元，+5分".format(net)
            else:
                details["divergence"] = "❌ 主力净流出 {:.0f} 万元".format(abs(net))
        else:
            details["divergence"] = "⚠️ moneyflow字段缺失"
    else:
        details["divergence"] = "⚠️ 无资金流向数据"

    # ── 组装 data_json（供 AI 使用）──────────────────────────────────────────
    data_json = {
        "ts_code":      ts_code,
        "name":         name,
        "industry":     industry,
        "trade_date":   latest["trade_date"],
        "close":        round(float(latest["close"]), 2),
        "pct_chg":      round(float(latest["pct_chg"]), 2),
        "vol":          round(float(latest["vol"]), 0),
        "amplitude_20d": round(float(amplitude), 4),
        "vol_ma20":     round(float(vol_ma20), 0),
        "big_candle_signal": kline_hit,
        "moneyflow":    {} if df_money.empty else df_money.iloc[0].to_dict(),
        "holder_latest": {} if df_holder.empty else df_holder.head(2).to_dict("records"),
        "python_score": score,
        "score_details": details,
    }

    # ── 初步评级（不含催化，满分70）────────────────────────────────────────
    result.update({
        "score":     score,
        "details":   details,
        "data_json": data_json,
    })
    return result


def _grade(total_score: int) -> str:
    if total_score >= 85: return "S"
    if total_score >= 70: return "A"
    if total_score >= 40: return "B"
    return "C"


# =============================================================================
# 全市场扫描
# =============================================================================
def scan_market(min_python_score: int = 25,
                max_stocks: int = None) -> pd.DataFrame:
    """
    扫描全市场，返回 python_score >= min_python_score 的候选股。
    min_python_score=25 意味着至少满足横盘条件，过滤掉明显无效标的。
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")

    # 读股票列表
    stock_df = pd.read_sql(
        "SELECT ts_code, name, industry FROM stock_list ORDER BY ts_code", conn
    )
    if max_stocks:
        stock_df = stock_df.head(max_stocks)

    total   = len(stock_df)
    results = []
    errors  = 0
    start_t = time.time()

    log.info("🔍 开始全市场扫描：%d 只股票", total)

    for i, row in enumerate(stock_df.itertuples(index=False), 1):
        try:
            r = score_one(row.ts_code, conn,
                          name=row.name, industry=row.industry)
            if r["veto"] is None and r["score"] >= min_python_score:
                results.append(r)
            errors = 0   # 成功后重置熔断计数
        except Exception as e:
            errors += 1
            log.warning("评分失败 %s: %s", row.ts_code, e)
            if errors >= CIRCUIT_BREAKER_LIMIT:
                log.error("⚡ 连续 %d 次错误，触发熔断！扫描中止", errors)
                break

        if i % 500 == 0 or i == total:
            elapsed = time.time() - start_t
            log.info("扫描进度 %d/%d (%.1f%%) | 候选: %d | 耗时 %.0fs",
                     i, total, i/total*100, len(results), elapsed)

    conn.close()

    if not results:
        log.info("🔚 扫描完成，无候选股")
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df["grade"] = df["score"].apply(_grade)
    df = df.sort_values("score", ascending=False).reset_index(drop=True)

    log.info("✅ 扫描完成：%d 只候选（python_score ≥ %d）", len(df), min_python_score)
    return df


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    # 快速测试：只扫前100只
    df = scan_market(min_python_score=10, max_stocks=100)
    print(df[["ts_code", "name", "industry", "score", "grade"]].to_string())

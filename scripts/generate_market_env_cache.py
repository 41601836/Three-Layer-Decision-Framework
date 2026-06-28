# -*- coding: utf-8 -*-
"""
scripts/generate_market_env_cache.py —— 大盘四色指标计算与缓存表生成脚本
=====================================================================
功能：
  1. 批量计算历史区间内每日的市场情绪、主力资金、市场宽度、上证指数趋势四大客观指标。
  2. 根据四色预警核心规则，确定每日大盘颜色（绿/黄/红/黑）与最高资产总仓位。
  3. 将结果持久化写入 SQLite 数据库的 market_env_cache 表，供回测和实时流程快速调用。

参数：
  --start   开始日期（YYYYMMDD，默认20250101）
  --end     结束日期（YYYYMMDD，默认20251231）
  --recreate 是否重建缓存表（默认False）
"""

import os
import sys
import sqlite3
import argparse
import logging
import json
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

# 导入主目录，以便使用 config 等
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# =============================================================================
# 数据库初始化
# =============================================================================
def init_db(conn, recreate=False):
    cursor = conn.cursor()
    if recreate:
        log.info("[DB] 正在重建 market_env_cache 表...")
        cursor.execute("DROP TABLE IF EXISTS market_env_cache")
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_env_cache (
            trade_date TEXT PRIMARY KEY,
            emotion_status TEXT,
            capital_status TEXT,
            width_status TEXT,
            trend_status TEXT,
            color TEXT,
            max_pos REAL,
            detail TEXT
        )
    """)
    conn.commit()
    log.info("[DB] market_env_cache 表初始化完成")


# =============================================================================
# 数据预加载与前置预热计算
# =============================================================================
def load_raw_data(conn, start_date, end_date):
    """
    为了计算 60 日滚动高低以及 MA60，我们将开始日期向前推 120 个交易日
    """
    pre_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")
    log.info(f"[LOAD] 正在加载计算数据，范围: {pre_start} ~ {end_date}")

    # 1. 股票日线（含 pre_close 以判断涨停）
    log.info("  加载股票日线中...")
    daily = pd.read_sql("""
        SELECT ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount
        FROM daily_prices
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(pre_start, end_date))

    # 2. 股票信息（获取股票中文名以识别ST）
    log.info("  加载股票列表信息中...")
    stock_info = pd.read_sql("""
        SELECT ts_code, name
        FROM stock_list
    """, conn)
    daily = daily.merge(stock_info, on="ts_code", how="left")
    daily["name"] = daily["name"].fillna("")

    # 3. 资金流数据
    log.info("  加载资金流数据中...")
    money = pd.read_sql("""
        SELECT ts_code, trade_date, buy_elg_amount, buy_lg_amount, sell_elg_amount, sell_lg_amount
        FROM moneyflow
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(pre_start, end_date))

    # 4. 指数行情 (上证指数 000001.SH)
    log.info("  加载上证指数行情中...")
    index = pd.read_sql("""
        SELECT ts_code, trade_date, close, vol, pct_chg
        FROM daily_index
        WHERE ts_code = '000001.SH' AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date
    """, conn, params=(pre_start, end_date))

    log.info(f"[LOAD] 加载完成。个股行情: {len(daily):,} 行 | 资金流: {len(money):,} 行 | 上证数据: {len(index)} 行")
    return daily, money, index


# =============================================================================
# 指标一：市场情绪计算（涨停连板系列）
# =============================================================================
def compute_emotion_metrics(daily):
    log.info("[EMOTION] 开始计算市场情绪指标（涨停、连板、晋级率、炸板率）...")
    df = daily.sort_values(["ts_code", "trade_date"]).copy()
    
    # 转换数值类型
    for col in ["close", "pre_close", "high", "pct_chg"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # 1. 计算个股涨停价与触碰涨停价
    # 创业板、科创板 (20%涨停)
    is_kc_cy = df["ts_code"].str.startswith("300") | df["ts_code"].str.startswith("301") | df["ts_code"].str.startswith("688")
    # 北交所 (30%涨停)
    is_bj = df["ts_code"].str.startswith("8") | df["ts_code"].str.startswith("43") | df["ts_code"].str.startswith("83")
    # ST 股 (5%涨停)
    is_st = df["name"].str.contains("ST") | df["name"].str.contains("st")
    
    # 默认 10%
    limit_pct = np.select(
        [is_kc_cy, is_bj, is_st],
        [19.9, 29.9, 4.9],
        default=9.9
    )
    
    # 判定收盘涨停：涨幅大于等于对应限制比例
    df["is_limit_close"] = df["pct_chg"] >= limit_pct

    # 判定日内曾触及涨停
    # 通过 high 涨幅大于等于 limit_pct - 0.1% 来近似触板（更保险地，直接对比 high 相对 pre_close 的涨幅）
    high_pct = (df["high"] - df["pre_close"]) / df["pre_close"] * 100
    df["is_limit_touch"] = high_pct >= (limit_pct - 0.1)

    # 判定炸板 (曾触碰涨停但收盘未涨停)
    df["is_limit_open"] = df["is_limit_touch"] & (~df["is_limit_close"])

    # 2. 计算每只股票的连续涨停天数 (连板高度)
    # 通过 shift 比对，判断连板
    df["limit_group"] = (~df["is_limit_close"]).groupby(df["ts_code"]).cumsum()
    df["con_limit_count"] = df.groupby(["ts_code", "limit_group"]).cumcount() + 1
    # 如果当天没有封板，连板数归零
    df.loc[~df["is_limit_close"], "con_limit_count"] = 0

    # 3. 按日期汇总计算每日情绪数据
    log.info("  正在按交易日汇总情绪状态...")
    
    # 每日收盘涨停数
    limit_close_cnt = df.groupby("trade_date")["is_limit_close"].sum()
    # 每日曾触板数
    limit_touch_cnt = df.groupby("trade_date")["is_limit_touch"].sum()
    # 每日炸板数
    limit_open_cnt = df.groupby("trade_date")["is_limit_open"].sum()
    # 每日最高连板高度
    max_con_limit = df.groupby("trade_date")["con_limit_count"].max()

    # 计算连板晋级率
    # 晋级定义：今日连板数 >= 2 且今日收盘涨停，除以昨日收盘涨停数
    # 我们找出今日连板数 >= 2 且今日涨停的个股数
    promotion_cnt = df[df["is_limit_close"] & (df["con_limit_count"] >= 2)].groupby("trade_date")["ts_code"].count()
    
    # 昨日封板数（用来除）
    # 我们对 limit_close_cnt 进行 shift(1) 来得到昨日封板数
    limit_close_prev = limit_close_cnt.shift(1)

    # 炸板率 = 炸板数 / 曾触板数
    explode_rate = limit_open_cnt / limit_touch_cnt.replace(0, np.nan)
    explode_rate = explode_rate.fillna(0)

    # 晋级率 = 晋级数 / 昨日封板数
    promotion_rate = promotion_cnt / limit_close_prev.replace(0, np.nan)
    promotion_rate = promotion_rate.fillna(0)

    emotion_df = pd.DataFrame({
        "limit_close_cnt": limit_close_cnt,
        "limit_touch_cnt": limit_touch_cnt,
        "limit_open_cnt": limit_open_cnt,
        "max_con_limit": max_con_limit,
        "explode_rate": explode_rate,
        "promotion_rate": promotion_rate
    }).reset_index()

    # 定性情绪状态
    # 积极信号：晋级率 >= 40% 且 最高连板 >= 5板
    # 退潮信号：晋级率 < 30% 或 炸板率 > 40%
    emotion_df["emotion_status"] = "neutral"
    
    is_positive = (emotion_df["promotion_rate"] >= 0.40) & (emotion_df["max_con_limit"] >= 5)
    is_negative = (emotion_df["promotion_rate"] < 0.30) | (emotion_df["explode_rate"] > 0.40)
    
    # 连退潮判断的例外：第一天如果啥都没（如昨日封板=0导致无法计算），避免误判为退潮
    is_valid_prev = limit_close_prev.reset_index(drop=True) > 2  # 昨日必须有一定数量的封板，晋级率才有意义
    is_negative = is_negative & emotion_df["trade_date"].isin(limit_close_prev[limit_close_prev > 2].index)

    emotion_df.loc[is_positive, "emotion_status"] = "positive"
    # 出逃/退潮是负向，直接定为 negative (触发即进🔴红色)
    emotion_df.loc[is_negative, "emotion_status"] = "negative"

    log.info(f"[EMOTION] 计算完成。最高连板中位数: {emotion_df['max_con_limit'].median():.0f} | 情绪积极天数: {emotion_df['emotion_status'].eq('positive').sum()} | 情绪退潮天数: {emotion_df['emotion_status'].eq('negative').sum()}")
    return emotion_df


# =============================================================================
# 指标二：主力资金计算（特大+大单）
# =============================================================================
def compute_capital_metrics(money, trade_dates):
    log.info("[CAPITAL] 开始计算主力资金净流入及近5日状态...")
    
    mdf = money.copy()
    for col in ["buy_elg_amount", "buy_lg_amount", "sell_elg_amount", "sell_lg_amount"]:
        mdf[col] = pd.to_numeric(mdf[col], errors="coerce").fillna(0)

    # 主力净额 = (特大买+大单买) - (特大卖+大单卖)
    mdf["net_main"] = (mdf["buy_elg_amount"] + mdf["buy_lg_amount"] - 
                       mdf["sell_elg_amount"] - mdf["sell_lg_amount"])
    
    # 按天汇总（万元）
    daily_capital = mdf.groupby("trade_date")["net_main"].sum().reset_index()
    daily_capital["net_main_yi"] = daily_capital["net_main"] / 10000.0  # 亿元

    # 计算近5日净流入天数
    daily_capital = daily_capital.sort_values("trade_date")
    daily_capital["is_inflow"] = daily_capital["net_main_yi"] > 0
    daily_capital["inflow_5d_cnt"] = daily_capital["is_inflow"].rolling(5).sum()

    # 资金判定定性
    # 积极信号：当日主力净流入 > 0 或 近5日中有3日及以上为净流入
    # 出逃信号：当日主力资金净流出超过 200亿 (net_main_yi < -200)
    daily_capital["capital_status"] = "neutral"
    
    is_positive = (daily_capital["net_main_yi"] > 0) | (daily_capital["inflow_5d_cnt"] >= 3)
    is_negative = daily_capital["net_main_yi"] < -200.0  # 净流出超200亿

    daily_capital.loc[is_positive, "capital_status"] = "positive"
    daily_capital.loc[is_negative, "capital_status"] = "negative"

    log.info(f"[CAPITAL] 计算完成。资金流入积极天数: {daily_capital['capital_status'].eq('positive').sum()} | 大幅出逃(>200亿)天数: {daily_capital['capital_status'].eq('negative').sum()}")
    return daily_capital


# =============================================================================
# 指标三：市场宽度计算（60日新高/新低）
# =============================================================================
def compute_width_metrics(daily):
    log.info("[WIDTH] 开始计算个股创 60 日新高、新低比值...")
    
    df = daily.sort_values(["ts_code", "trade_date"]).copy()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")

    # 向量化计算 60日高低点
    df["high_60d"] = df.groupby("ts_code")["close"].transform(lambda x: x.rolling(60).max())
    df["low_60d"] = df.groupby("ts_code")["close"].transform(lambda x: x.rolling(60).min())

    df["is_new_high"] = df["close"] == df["high_60d"]
    df["is_new_low"] = df["close"] == df["low_60d"]

    # 过滤掉上市不足60天的无意义高低点
    df["cnt_60d"] = df.groupby("ts_code")["close"].transform(lambda x: x.rolling(60).count())
    df.loc[df["cnt_60d"] < 60, ["is_new_high", "is_new_low"]] = False

    # 按天汇总
    new_high_cnt = df.groupby("trade_date")["is_new_high"].sum()
    new_low_cnt = df.groupby("trade_date")["is_new_low"].sum()

    width_df = pd.DataFrame({
        "new_high_cnt": new_high_cnt,
        "new_low_cnt": new_low_cnt
    }).reset_index()

    # 计算比例 R = 新高数 / 新低数
    # 新低数为0时做安全替换
    width_df["ratio_R"] = width_df["new_high_cnt"] / width_df["new_low_cnt"].replace(0, np.nan)
    width_df["ratio_R"] = width_df["ratio_R"].fillna(width_df["new_high_cnt"])  # 新低为0时，比值就是新高数本身

    # 定性宽度状态
    # 积极信号：R > 1.5
    # 弱势信号：R < 0.8
    width_df["width_status"] = "neutral"
    is_positive = width_df["ratio_R"] > 1.5
    is_negative = width_df["ratio_R"] < 0.8

    width_df.loc[is_positive, "width_status"] = "positive"
    width_df.loc[is_negative, "width_status"] = "negative"

    log.info(f"[WIDTH] 计算完成。宽度积极天数: {width_df['width_status'].eq('positive').sum()} | 宽度弱势天数: {width_df['width_status'].eq('negative').sum()}")
    return width_df


# =============================================================================
# 指标四：指数趋势计算（上证 MA60 连续3日放量下跌）
# =============================================================================
def compute_trend_metrics(index):
    log.info("[TREND] 开始计算上证指数 MA60 及连续3日放量下跌...")
    
    idx_df = index.sort_values("trade_date").copy()
    idx_df["close"] = pd.to_numeric(idx_df["close"], errors="coerce")
    idx_df["vol"] = pd.to_numeric(idx_df["vol"], errors="coerce")

    # 1. 均线计算
    idx_df["ma60"] = idx_df["close"].rolling(60).mean()

    # 2. 判断收盘低于 MA60
    idx_df["below_ma60"] = idx_df["close"] < idx_df["ma60"]

    # 3. 判定放量下跌：收盘下跌（或pct_chg < 0）且 成交量放大
    # 成交量放大：vol > prev_vol
    idx_df["vol_prev"] = idx_df["vol"].shift(1)
    idx_df["close_prev"] = idx_df["close"].shift(1)
    idx_df["is_drop"] = idx_df["close"] < idx_df["close_prev"]
    idx_df["is_vol_up"] = idx_df["vol"] > idx_df["vol_prev"]

    # 4. 判断连续3日：低于 MA60 & 放量 & 下跌
    # 条件一：近3日每日 close < ma60
    cond_below = idx_df["below_ma60"].rolling(3).sum() == 3
    # 条件二：近3日每日都是下跌的
    cond_drop = idx_df["is_drop"].rolling(3).sum() == 3
    # 条件三：这3日的成交量递增（vol_t > vol_t-1 > vol_t-2）
    idx_df["vol_up_2d"] = idx_df["is_vol_up"] & idx_df["is_vol_up"].shift(1) # t > t-1 且 t-1 > t-2
    cond_vol_up = idx_df["vol_up_2d"].rolling(1).sum() == 1  # 只要当天且昨日成交量递增

    # 整合系统风险：触发即黑色
    idx_df["trend_status"] = "normal"
    risk_triggered = cond_below & cond_drop & cond_vol_up
    idx_df.loc[risk_triggered, "trend_status"] = "risk"

    log.info(f"[TREND] 计算完成。系统风险触发天数: {idx_df['trend_status'].eq('risk').sum()}")
    return idx_df


# =============================================================================
# 四色整合判定
# =============================================================================
def determine_final_colors(emotion, capital, width, trend):
    log.info("[COLOR] 整合四大指标进行最终颜色与仓位判定...")
    
    # 以交易日对齐合并
    m1 = emotion[["trade_date", "limit_close_cnt", "limit_touch_cnt", "limit_open_cnt", "max_con_limit", "explode_rate", "promotion_rate", "emotion_status"]]
    m2 = capital[["trade_date", "net_main_yi", "inflow_5d_cnt", "capital_status"]]
    m3 = width[["trade_date", "new_high_cnt", "new_low_cnt", "ratio_R", "width_status"]]
    m4 = trend[["trade_date", "close", "ma60", "vol", "trend_status"]]

    res = m1.merge(m2, on="trade_date", how="inner")
    res = res.merge(m3, on="trade_date", how="inner")
    res = res.merge(m4, on="trade_date", how="inner")

    # 四色规则：
    # 黑色：触发系统性风险 (trend_status == 'risk') → 仓位 0%
    # 红色：触发情绪退潮 (emotion_status == 'negative') 或 资金出逃 (capital_status == 'negative') 或 宽度弱势 (width_status == 'negative') → 仓位 20%
    # 绿色：情绪与资金均积极 (emotion == 'positive' and capital == 'positive') 且无红色/黑色触发 → 仓位 70%
    # 黄色：情绪或资金有一项积极，且无红色/黑色触发 → 仓位 40%
    # 其余情况（震荡，双中性）：归为红色或黄色？
    # 根据用户规则：
    # “🟡 黄色 谨慎参与：情绪 OR 资金有一项积极，且无红色触发”
    # “🟢 绿色 积极进攻：情绪 AND 资金均积极”
    # “🔴 红色 防守休息：触发情绪退潮 OR 资金出逃信号”
    # “⚫ 黑色 极端休战：触发系统性风险信号”
    # 那么，如果是“双中性”，即无任何一项积极，但也没有触发任何退潮/出逃信号，这属于哪种？
    # 一般定性为 🔴 红色（防守休息，防守仓位 20%）或者 黄色？
    # 用户明确提到：情绪 OR 资金有一项积极，且无红色触发 → 黄色。
    # 那么“双中性且没有负向触发”应该判定为 🔴 红色（防守休息，最高20%）。因为不积极就是防守，非常合理！
    
    colors = []
    max_pos = []
    
    for idx, row in res.iterrows():
        t_status = row["trend_status"]
        e_status = row["emotion_status"]
        c_status = row["capital_status"]
        w_status = row["width_status"]

        if t_status == "risk":
            # 黑色
            colors.append("black")
            max_pos.append(0.00)
        elif e_status == "negative" or c_status == "negative" or w_status == "negative":
            # 红色
            colors.append("red")
            max_pos.append(0.20)
        elif e_status == "positive" and c_status == "positive":
            # 绿色
            colors.append("green")
            max_pos.append(0.70)
        elif e_status == "positive" or c_status == "positive":
            # 黄色
            colors.append("yellow")
            max_pos.append(0.40)
        else:
            # 双中性，无红色触发，定为红色（防守休息）
            colors.append("red")
            max_pos.append(0.20)

    res["color"] = colors
    res["max_pos"] = max_pos
    
    from collections import Counter
    cnt = Counter(colors)
    log.info(f"[COLOR] 判定完成。🟢绿色: {cnt['green']} 天 | 🟡黄色: {cnt['yellow']} 天 | 🔴红色: {cnt['red']} 天 | ⚫黑色: {cnt['black']} 天")
    return res


# =============================================================================
# 保存结果至缓存表
# =============================================================================
def save_to_cache_db(conn, df, start_date):
    """只保存落入 start_date 之后区间的数据以避免未来泄露，并覆盖写入"""
    cursor = conn.cursor()
    
    valid_df = df[df["trade_date"] >= start_date].copy()
    log.info(f"[SAVE] 正在保存结果到 DB (共 {len(valid_df)} 天)...")
    
    n_saved = 0
    for _, row in valid_df.iterrows():
        trade_date = row["trade_date"]
        
        detail_dict = {
            "limit_close_cnt": int(row["limit_close_cnt"]),
            "limit_touch_cnt": int(row["limit_touch_cnt"]),
            "limit_open_cnt": int(row["limit_open_cnt"]),
            "max_con_limit": int(row["max_con_limit"]),
            "explode_rate": float(row["explode_rate"]),
            "promotion_rate": float(row["promotion_rate"]),
            "net_main_yi": float(row["net_main_yi"]),
            "inflow_5d_cnt": int(row["inflow_5d_cnt"]),
            "new_high_cnt": int(row["new_high_cnt"]),
            "new_low_cnt": int(row["new_low_cnt"]),
            "ratio_R": float(row["ratio_R"]),
            "sh_close": float(row["close"]),
            "sh_ma60": float(row["ma60"]),
            "sh_vol": float(row["vol"])
        }
        
        cursor.execute("""
            INSERT OR REPLACE INTO market_env_cache (
                trade_date, emotion_status, capital_status, width_status, trend_status, color, max_pos, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_date,
            row["emotion_status"],
            row["capital_status"],
            row["width_status"],
            row["trend_status"],
            row["color"],
            float(row["max_pos"]),
            json.dumps(detail_dict, ensure_ascii=False)
        ))
        n_saved += 1

    conn.commit()
    log.info(f"[SAVE] 保存完成，共写入 {n_saved} 条记录。")


def main():
    parser = argparse.ArgumentParser(description="大盘指标四色预警离线缓存生成器")
    parser.add_argument("--start", default="20250101", help="回测开始日期")
    parser.add_argument("--end", default="20251231", help="回测结束日期")
    parser.add_argument("--recreate", action="store_true", help="是否重建表结构")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    try:
        init_db(conn, args.recreate)
        
        daily, money, index = load_raw_data(conn, args.start, args.end)
        
        if daily.empty or money.empty or index.empty:
            log.error("本地数据不足，无法生成缓存。请先同步数据！")
            return
            
        trade_dates = sorted(daily["trade_date"].unique().tolist())
        
        emotion_df = compute_emotion_metrics(daily)
        capital_df = compute_capital_metrics(money, trade_dates)
        width_df = compute_width_metrics(daily)
        trend_df = compute_trend_metrics(index)
        
        result_df = determine_final_colors(emotion_df, capital_df, width_df, trend_df)
        
        save_to_cache_db(conn, result_df, args.start)
        
    finally:
        conn.close()
        
    log.info("🎉 缓存生成工作顺利完成！")


if __name__ == "__main__":
    main()

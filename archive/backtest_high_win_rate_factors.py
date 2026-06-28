# -*- coding: utf-8 -*-
"""
高胜率多因子组合回测脚本 (预测胜率提高版)
================================================
本脚本用于回测以下极端严苛的多因子组合方案：
  1. 获利盘占比 >= 80% (若当天包含筹码数据)
  2. 筹码峰集中度 (单峰占比) >= 20% (若当天包含筹码数据)
  3. 主力净流入强度 >= 15%
  4. 板块近5日涨幅排名 <= 20% (前20%)
  5. 5日动量加速度 > 0
  6. 当日涨幅 >= 2%
  7. 流通市值 >= 50亿
  8. 大盘MA20 > MA60 (上证指数)
"""

import os
import sys
import sqlite3
import argparse
import time
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")
sys.path.insert(0, ROOT_DIR)

# 止损与参数配置
STOP_LOSS_PCT   = 0.08   # 8%固定止损
HOLD_DAYS       = 10     # 最长持股天数

# 仓位比例
TOTAL_POS_LIMITS = {
    "green":  0.70,
    "yellow": 0.40,
    "red":    0.20,
    "black":  0.00,
}

SINGLE_POS_LIMITS = {
    "green":  0.15,
    "yellow": 0.10,
    "red":    0.05,
    "black":  0.00,
}


def log_info(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [INFO] {msg}")


# =============================================================================
# 数据加载
# =============================================================================
def load_all_data(conn, start_date, end_date):
    pre_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")
    log_info(f"[START] 开启高胜率多因子回测，区间: {start_date} ~ {end_date}")

    print("[LOAD] 日线数据与行业...")
    # 关联 stock_list 获取行业
    daily = pd.read_sql("""
        SELECT dp.ts_code, dp.trade_date, dp.open, dp.high, dp.low, dp.close, dp.pct_chg, dp.vol, dp.amount, sl.industry
        FROM daily_prices dp
        LEFT JOIN stock_list sl ON dp.ts_code = sl.ts_code
        WHERE dp.trade_date BETWEEN ? AND ?
        ORDER BY dp.ts_code, dp.trade_date
    """, conn, params=(pre_start, end_date))

    print("[LOAD] 资金流数据...")
    money = pd.read_sql("""
        SELECT ts_code, trade_date,
               buy_elg_amount, sell_elg_amount,
               buy_lg_amount, sell_lg_amount
        FROM moneyflow
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(start_date, end_date))

    print("[LOAD] 股东户数数据...")
    holder = pd.read_sql("""
        SELECT ts_code, ann_date, holder_num
        FROM stk_holdernumber
        ORDER BY ts_code, ann_date
    """, conn)

    print("[LOAD] 流通市值数据...")
    try:
        # 每日动态流通市值
        circ_mv = pd.read_sql("""
            SELECT ts_code, trade_date, circ_mv
            FROM daily_basic
            WHERE trade_date BETWEEN ? AND ? AND circ_mv IS NOT NULL
        """, conn, params=(start_date, end_date))
        circ_mv["circ_mv_yi"] = pd.to_numeric(circ_mv["circ_mv"], errors="coerce") / 10000.0
    except Exception as e:
        print(f"  [WARN] 无法加载动态流通市值: {e}")
        circ_mv = pd.DataFrame(columns=["ts_code", "trade_date", "circ_mv_yi"])

    print("[LOAD] 筹码分布数据...")
    # 由于 cyq_chips 通常非常庞大，我们只加载有数据的日期
    try:
        chips = pd.read_sql("""
            SELECT ts_code, trade_date, price, percent
            FROM cyq_chips
            WHERE trade_date BETWEEN ? AND ?
        """, conn, params=(start_date, end_date))
    except Exception as e:
        print(f"  [WARN] 无法加载筹码数据: {e}")
        chips = pd.DataFrame(columns=["ts_code", "trade_date", "price", "percent"])

    print(f"[DATA] 日线:{len(daily):,} | 资金:{len(money):,} | 股东:{len(holder):,} | "
          f"市值:{len(circ_mv):,} | 筹码:{len(chips):,}")
    return daily, money, holder, circ_mv, chips


# =============================================================================
# 因子计算 & 信号生成
# =============================================================================
def compute_signals(daily_all, money, holder, circ_mv, chips_raw, start_date, conn, use_composite=False):
    from market_env import get_market_mode
    
    daily = daily_all[daily_all["trade_date"] >= start_date].copy()
    
    # 预计算每日市场状态
    print("[MARKET] 预计算每日市场状态...")
    trade_dates = sorted(daily["trade_date"].unique())
    market_mode_map = {}
    for date in trade_dates:
        mode, _, _ = get_market_mode(conn=conn, target_date=date, persist=False)
        market_mode_map[date] = mode
    
    # 统计各状态天数
    from collections import Counter
    mode_counts = Counter(market_mode_map.values())
    print(f"  市场状态分布: green={mode_counts.get('green',0)} | yellow={mode_counts.get('yellow',0)} | red={mode_counts.get('red',0)} | black={mode_counts.get('black',0)}")

    # 1. 计算板块近5日涨幅排名 (对齐到全市场日线)
    print("[FACTOR] 计算板块近5日平均涨幅及百分比排名...")
    # 获取申万行业不为空的日线数据
    df_ind_p = daily_all[["trade_date", "industry", "pct_chg"]].dropna()
    df_ind_p = df_ind_p[df_ind_p["industry"] != ""]
    
    # 行业每日平均涨幅
    ind_daily = df_ind_p.groupby(["industry", "trade_date"])["pct_chg"].mean().reset_index()
    ind_daily = ind_daily.sort_values(["industry", "trade_date"])
    # 5日滚动累计涨幅
    ind_daily["pct_chg_5d"] = ind_daily.groupby("industry")["pct_chg"].transform(lambda x: x.rolling(5).sum())
    # 板块排名百分比 (降序，数值越小代表收益越高，前20% 即 <= 0.20)
    ind_daily["ind_rank_pct"] = ind_daily.groupby("trade_date")["pct_chg_5d"].rank(pct=True, ascending=False)
    
    # 合并板块排名到个股日线
    df = daily.merge(ind_daily[["industry", "trade_date", "ind_rank_pct"]], on=["industry", "trade_date"], how="left")
    df["ind_rank_pct"] = df["ind_rank_pct"].fillna(1.0) # 未找到排名的默认垫底

    # 2. 计算主力净流入强度
    print("[FACTOR] 计算主力净流入强度 (已处理量纲陷阱)...")
    money = money.copy()
    for col in ["buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount"]:
        money[col] = pd.to_numeric(money[col], errors="coerce").fillna(0)
    # 主力净流入 (万元)
    money["net_main_wan"] = (money["buy_elg_amount"] + money["buy_lg_amount"]
                             - money["sell_elg_amount"] - money["sell_lg_amount"])
    # 合并主力净流入
    df = df.merge(money[["ts_code", "trade_date", "net_main_wan"]], on=["ts_code", "trade_date"], how="left")
    df["net_main_wan"] = df["net_main_wan"].fillna(0.0)

    # 修正 amount 量纲 (如果 amount < vol * close 则说明单位为千元，需乘以 1000 换算为元)
    df["amount_yuan"] = df["amount"]
    thousand_mask = df["amount"] < (df["vol"] * df["close"])
    df.loc[thousand_mask, "amount_yuan"] = df.loc[thousand_mask, "amount"] * 1000.0

    # 计算主力净流入强度: (主力净流入 * 10000) / 成交额 (元)
    df["net_main_intensity"] = (df["net_main_wan"] * 10000.0) / df["amount_yuan"].replace(0, np.nan)
    df["net_main_intensity"] = df["net_main_intensity"].fillna(0.0)

    # 3. 计算 5 日动量加速度与其它辅助因子
    print("[FACTOR] 计算5日动量加速度与振幅收缩/均线共振 (T-1 延迟)...")
    daily_all = daily_all.sort_values(["ts_code", "trade_date"]).copy()
    daily_all["momentum_3d"] = daily_all.groupby("ts_code")["close"].transform(lambda x: x.pct_change(3).shift(1))
    daily_all["momentum_5d"] = daily_all.groupby("ts_code")["close"].transform(lambda x: x.pct_change(5).shift(1))
    daily_all["momentum_accel"] = daily_all["momentum_3d"] - daily_all["momentum_5d"]
    
    daily_all["high_20"] = daily_all.groupby("ts_code")["high"].transform(lambda x: x.rolling(20).max().shift(1))
    daily_all["low_20"] = daily_all.groupby("ts_code")["low"].transform(lambda x: x.rolling(20).min().shift(1))
    daily_all["amp_20"] = (daily_all["high_20"] - daily_all["low_20"]) / daily_all["low_20"].replace(0, np.nan)
    daily_all["ma5"] = daily_all.groupby("ts_code")["close"].transform(lambda x: x.rolling(5).mean())
    daily_all["ma20"] = daily_all.groupby("ts_code")["close"].transform(lambda x: x.rolling(20).mean())
    daily_all["ma60"] = daily_all.groupby("ts_code")["close"].transform(lambda x: x.rolling(60).mean())
    daily_all["bullish"] = (daily_all["ma5"] > daily_all["ma20"]) & (daily_all["ma20"] > daily_all["ma60"])
    
    # 计算成交量指标
    daily_all["vol_ma5"] = daily_all.groupby("ts_code")["vol"].transform(lambda x: x.rolling(5).mean())
    daily_all["vol_over_ma5"] = daily_all["vol"] > (daily_all["vol_ma5"] * 1.2)
    
    # 计算MACD指标
    daily_all["ema12"] = daily_all.groupby("ts_code")["close"].transform(lambda x: x.ewm(span=12, adjust=False).mean())
    daily_all["ema26"] = daily_all.groupby("ts_code")["close"].transform(lambda x: x.ewm(span=26, adjust=False).mean())
    daily_all["dif"] = daily_all["ema12"] - daily_all["ema26"]
    daily_all["dea"] = daily_all.groupby("ts_code")["dif"].transform(lambda x: x.ewm(span=9, adjust=False).mean())
    daily_all["macd_bar"] = (daily_all["dif"] - daily_all["dea"]) * 2
    
    # MACD金叉或柱状线翻红
    daily_all["macd_gold_cross"] = (daily_all["dif"] > daily_all["dea"]) & (daily_all["dif"].shift(1) <= daily_all["dea"].shift(1))
    daily_all["macd_bar_red"] = (daily_all["macd_bar"] > 0) & (daily_all["macd_bar"].shift(1) <= 0)
    daily_all["macd_confirmed"] = daily_all["macd_gold_cross"] | daily_all["macd_bar_red"]
    
    # 计算RSI指标（用于严格确认条件）
    daily_all["price_change"] = daily_all.groupby("ts_code")["close"].transform(lambda x: x.diff())
    daily_all["up_change"] = daily_all["price_change"].apply(lambda x: x if x > 0 else 0)
    daily_all["down_change"] = daily_all["price_change"].apply(lambda x: abs(x) if x < 0 else 0)
    daily_all["avg_up"] = daily_all.groupby("ts_code")["up_change"].transform(lambda x: x.rolling(14).mean())
    daily_all["avg_down"] = daily_all.groupby("ts_code")["down_change"].transform(lambda x: x.rolling(14).mean())
    daily_all["rsi"] = daily_all.apply(lambda row: 100 - (100 / (1 + row["avg_up"] / row["avg_down"])) if row["avg_down"] != 0 else 100, axis=1)
    daily_all["rsi"] = daily_all["rsi"].fillna(50)
    
    # 成交量条件（宽松和严格版本）
    daily_all["vol_over_ma5_loose"] = daily_all["vol"] > (daily_all["vol_ma5"] * 1.2)  # 宽松: 1.2倍
    daily_all["vol_over_ma5_strict"] = daily_all["vol"] > (daily_all["vol_ma5"] * 1.5)  # 严格: 1.5倍
    daily_all["rsi_above_50"] = daily_all["rsi"] > 50
    
    # 超跌反弹复合评分所需因子
    daily_all["drop_20d"] = daily_all.groupby("ts_code")["close"].transform(lambda x: x.pct_change(20))
    daily_all["vol_ratio_5d"] = daily_all["vol"] / daily_all["vol_ma5"].replace(0, np.nan)
    daily_all["price_ma20_ratio"] = daily_all["close"] / daily_all["ma20"].replace(0, np.nan)
    daily_all["rsi_14"] = daily_all["rsi"]

    # 合并特征到推理主表
    df = df.merge(daily_all[["ts_code", "trade_date", "momentum_accel", "amp_20", "ma5", "ma20", "ma60", "bullish", 
                             "vol_over_ma5_loose", "vol_over_ma5_strict", "macd_confirmed", "rsi_above_50",
                             "drop_20d", "vol_ratio_5d", "price_ma20_ratio", "rsi_14"]], 
                  on=["ts_code", "trade_date"], how="left")
    df["momentum_accel"] = df["momentum_accel"].fillna(0.0)
    df["amp_20"] = df["amp_20"].fillna(0.20)
    df["ma5"] = df["ma5"].fillna(df["close"])

    # 4. 合并动态流通市值
    print("[FACTOR] 合并流通市值数据...")
    df = df.merge(circ_mv[["ts_code", "trade_date", "circ_mv_yi"]], on=["ts_code", "trade_date"], how="left")
    # 如果缺动态流通市值，使用该股票的最后一个非空市值填充
    if "circ_mv_yi" in df.columns:
        df["circ_mv_yi"] = df.groupby("ts_code")["circ_mv_yi"].ffill().bfill().fillna(0.0)
    else:
        df["circ_mv_yi"] = 0.0

    # 5. 计算筹码特征
    # 如果筹码表中有当前日期的筹码数据，进行聚合计算
    df["winner_rate"] = 1.0  # 默认无筹码数据时设为 1.0 (通过)
    df["chips_peak_pct"] = 25.0  # 默认无筹码数据时设为 25.0 (通过)
    
    if not chips_raw.empty:
        print("[FACTOR] 计算筹码获利盘与单峰集中度...")
        chips_merged = chips_raw.merge(df[["ts_code", "trade_date", "close"]], on=["ts_code", "trade_date"], how="inner")
        # 获利盘
        chips_merged["is_win"] = chips_merged["price"] <= chips_merged["close"]
        winner_df = chips_merged.groupby(["ts_code", "trade_date"]).apply(
            lambda x: x[x["is_win"]]["percent"].sum() / 100.0
        ).reset_index(name="calc_winner_rate")
        
        # 筹码峰值集中度 (最大单价格比例)
        peak_df = chips_raw.groupby(["ts_code", "trade_date"])["percent"].max().reset_index(name="calc_chips_peak")
        
        # 合并回主表
        df = df.merge(winner_df, on=["ts_code", "trade_date"], how="left")
        df = df.merge(peak_df, on=["ts_code", "trade_date"], how="left")
        
        # 如果有计算值，则覆盖默认值
        has_winner = df["calc_winner_rate"].notna()
        df.loc[has_winner, "winner_rate"] = df.loc[has_winner, "calc_winner_rate"]
        
        has_peak = df["calc_chips_peak"].notna()
        df.loc[has_peak, "chips_peak_pct"] = df.loc[has_peak, "calc_chips_peak"]
        
        df.drop(columns=["calc_winner_rate", "calc_chips_peak"], errors="ignore", inplace=True)

    # 6. 大盘择时 (上证指数 MA20 > MA60)
    print("[TIMING] 计算上证大盘择时过滤...")
    try:
        idx_df = pd.read_sql("""SELECT trade_date, close FROM daily_index WHERE ts_code='000001.SH' ORDER BY trade_date""", conn)
        idx_df['ma20'] = idx_df['close'].rolling(20).mean()
        idx_df['ma60'] = idx_df['close'].rolling(60).mean()
        safe_dates = idx_df[idx_df['ma20'] > idx_df['ma60']]['trade_date'].tolist()
        df = df[df['trade_date'].isin(safe_dates)]
    except Exception as e:
        print('  [WARN] 大盘过滤计算异常:', e)

    # =============================================================================
    # 方案B: 引入市场状态动态阈值（最终版）
    # =============================================================================
    # 动态阈值配置 - 包含仓位系数
    DYNAMIC_THRESHOLDS = {
        "green": {
            "获利盘": 0.75,
            "主力强度": 0.11,
            "板块排名": 0.25,
            "涨幅": 0.015,
            "确认方式": "loose",
            "仓位系数": 1.0
        },
        "yellow": {
            "获利盘": 0.70,
            "主力强度": 0.08,
            "板块排名": 0.30,
            "涨幅": 0.010,
            "确认方式": "loose",
            "仓位系数": 0.7
        },
        "red": {
            "获利盘": 0.68,      # 放宽至0.68
            "主力强度": 0.06,    # 放宽至0.06
            "板块排名": 0.38,    # 保持0.38
            "涨幅": 0.005,       # 保持0.005
            "确认方式": "loose",  # 宽松确认
            "仓位系数": 0.35     # 仓位系数0.35
        },
        "black": {
            "交易": False  # 禁止交易
        }
    }
    
    # 合并市场状态到DataFrame
    df["market_mode"] = df["trade_date"].map(market_mode_map)
    
    # 根据市场状态应用不同阈值
    def get_threshold(mode, key, default):
        config = DYNAMIC_THRESHOLDS.get(mode, DYNAMIC_THRESHOLDS["red"])
        return config.get(key, default)
    
    df["threshold_winner"] = df["market_mode"].map(lambda m: get_threshold(m, "获利盘", 0.70))
    df["threshold_main"] = df["market_mode"].map(lambda m: get_threshold(m, "主力强度", 0.10))
    df["threshold_sector"] = df["market_mode"].map(lambda m: get_threshold(m, "板块排名", 0.30))
    df["threshold_pct"] = df["market_mode"].map(lambda m: get_threshold(m, "涨幅", 0.010) * 100)  # 转换为百分比
    df["confirm_method"] = df["market_mode"].map(lambda m: get_threshold(m, "确认方式", "strict"))
    df["position_coeff"] = df["market_mode"].map(lambda m: get_threshold(m, "仓位系数", 0.4))
    
    # 基础条件
    cond_peak = df["chips_peak_pct"] >= 18.0        # 筹码集中度 >=18%
    cond_accel = df["momentum_accel"] > 0.0          # 动量加速度>0
    cond_pct = df["pct_chg"] >= df["threshold_pct"]  # 动态涨幅阈值
    cond_mv = df["circ_mv_yi"] >= 50.0              # 流通市值 >= 50 亿
    cond_amp = df["amp_20"] <= 0.18                 # 振幅<=18%
    cond_ma5 = df["close"] >= df["ma5"]             # 站上5日均线
    
    # 均线多头排列
    cond_bullish = df["bullish"] == True
    
    # 宽松确认条件
    cond_loose_vol = df["vol_over_ma5_loose"] == True    # 成交量 > 5日均量 × 1.2
    cond_loose_macd = df["macd_confirmed"] == True       # MACD金叉或柱状线翻红
    cond_loose = cond_bullish | (cond_loose_vol & cond_loose_macd)
    
    # 严格确认条件
    cond_strict = cond_bullish  # 仅均线多头
    
    # 根据配置的确认方式选择确认条件
    is_loose = df["confirm_method"] == "loose"
    df["cond_trend_confirmed"] = df.apply(
        lambda row: cond_loose.loc[row.name] if row["confirm_method"] == "loose" else cond_strict.loc[row.name],
        axis=1
    )
    
    # 动态条件（根据市场状态调整）
    cond_winner = df["winner_rate"] >= df["threshold_winner"]
    cond_main_flow = df["net_main_intensity"] >= df["threshold_main"]
    cond_sector = df["ind_rank_pct"] <= df["threshold_sector"]
    
    if use_composite:
        # 超跌反弹复合评分模式
        print("[SIGNAL] 使用超跌反弹复合评分模式...")
        df["rebound_score"] = df.apply(calculate_rebound_score, axis=1)
        df["signal"] = df["rebound_score"] >= 80  # 提高阈值至80分
        
        # 额外过滤条件
        df["signal"] = df["signal"] & (df["circ_mv_yi"] >= 50.0)  # 流通市值 >= 50亿
        
        n_strong = df["signal"].sum()
        print(f"[SIGNAL] 超跌反弹复合评分信号数: {n_strong:,} 条")
        print(f"  评分分布: >=80分: {n_strong}, <80分: {len(df) - n_strong}")
    else:
        # 原始多因子模式
        # 合并所有条件（包含确认条件）
        df["signal"] = cond_winner & cond_peak & cond_main_flow & cond_sector & cond_accel & cond_pct & cond_mv & cond_amp & cond_ma5 & df["cond_trend_confirmed"]
        
        # 黑色状态强制不交易
        df.loc[df["market_mode"] == "black", "signal"] = False
        
        n_strong = df["signal"].sum()
        print(f"[SIGNAL] 严苛筛选强信号数: {n_strong:,} 条")
        
        # 按市场状态统计信号分布
        signal_by_mode = df[df["signal"] == True]["market_mode"].value_counts()
        print(f"  信号分布: green={signal_by_mode.get('green',0)} | yellow={signal_by_mode.get('yellow',0)} | red={signal_by_mode.get('red',0)} | black={signal_by_mode.get('black',0)}")
    
    return df


# =============================================================================
# 真实资产投资组合逐日持仓回测模拟
# =============================================================================
def run_portfolio_simulation(daily_all, signals_df, trade_dates, conn):
    from market_env import get_market_mode
    
    # 1. 预建股票价格索引
    print("[BACKTEST] 建立股票历史行情索引...")
    price_by_code = {}
    for code, grp in daily_all.sort_values("trade_date").groupby("ts_code"):
        price_by_code[code] = grp[["trade_date", "open", "high", "low", "close"]].reset_index(drop=True)

    # 提取低点（用于结构止损）
    def get_low20(code, entry_date):
        df_p = price_by_code.get(code)
        if df_p is None:
            return None
        past = df_p[df_p["trade_date"] <= entry_date].tail(20)
        if past.empty:
            return None
        return pd.to_numeric(past["low"], errors="coerce").min()

    # 提取强信号（包含仓位系数）
    strong_signals = signals_df[signals_df["signal"] == True].copy()
    signals_by_date = {}
    position_coeff_by_date_code = {}
    for dt, grp in strong_signals.groupby("trade_date"):
        signals_by_date[dt] = grp["ts_code"].tolist()
        for _, row in grp.iterrows():
            position_coeff_by_date_code[(dt, row["ts_code"])] = row["position_coeff"]

    # 2. 投资组合模拟参数
    initial_capital = 1000000.0
    cash = initial_capital
    active_positions = {}  # ts_code -> {entry_date, entry_price, stop_price, shares, current_price, hold_days}
    
    trade_history = []
    portfolio_equity = []
    
    n_stopped = 0
    n_expired = 0
    n_black_cleared = 0

    print("[BACKTEST] 开始投资组合每日交易与限仓模拟...")
    for idx, date in enumerate(trade_dates):
        # 1. 获取今日大盘颜色状态
        color, max_total_pos, _ = get_market_mode(conn=conn, target_date=date, persist=False)
        single_pos_pct = SINGLE_POS_LIMITS.get(color, 0.05)

        # 2. 黑色环境清仓处理
        if color == "black":
            cleared_codes = list(active_positions.keys())
            for code in cleared_codes:
                pos = active_positions[code]
                df_p = price_by_code.get(code)
                today_row = df_p[df_p["trade_date"] == date] if df_p is not None else None
                
                # 以开盘价强制平仓
                exit_price = float(today_row.iloc[0]["open"]) if today_row is not None and not today_row.empty else pos["current_price"]
                cash += exit_price * pos["shares"]
                
                trade_history.append({
                    "ts_code": code,
                    "entry_date": pos["entry_date"],
                    "exit_date": date,
                    "entry_price": pos["entry_price"],
                    "exit_price": exit_price,
                    "shares": pos["shares"],
                    "exit_pct": (exit_price - pos["entry_price"]) / pos["entry_price"] * 100,
                    "reason": "⚫ 黑色大盘强制清仓",
                    "hold_days": pos["hold_days"]
                })
                n_black_cleared += 1
            active_positions.clear()

        else:
            # 3. 日常持仓平仓检测（非黑色环境下）
            closed_codes = []
            for code, pos in active_positions.items():
                df_p = price_by_code.get(code)
                if df_p is None:
                    closed_codes.append(code)
                    continue
                
                today_row = df_p[df_p["trade_date"] == date]
                if today_row.empty:
                    continue
                
                day_low = float(today_row.iloc[0]["low"])
                day_close = float(today_row.iloc[0]["close"])
                
                pos["hold_days"] += 1
                pos["current_price"] = day_close

                # A. 止损触板检测
                if day_low <= pos["stop_price"]:
                    exit_price = pos["stop_price"]
                    cash += exit_price * pos["shares"]
                    trade_history.append({
                        "ts_code": code,
                        "entry_date": pos["entry_date"],
                        "exit_date": date,
                        "entry_price": pos["entry_price"],
                        "exit_price": exit_price,
                        "shares": pos["shares"],
                        "exit_pct": (exit_price - pos["entry_price"]) / pos["entry_price"] * 100,
                        "reason": "🔴 触发止损",
                        "hold_days": pos["hold_days"]
                    })
                    closed_codes.append(code)
                    n_stopped += 1
                    continue
                
                # B. 到期平仓检测
                if pos["hold_days"] >= HOLD_DAYS:
                    exit_price = day_close
                    cash += exit_price * pos["shares"]
                    trade_history.append({
                        "ts_code": code,
                        "entry_date": pos["entry_date"],
                        "exit_date": date,
                        "entry_price": pos["entry_price"],
                        "exit_price": exit_price,
                        "shares": pos["shares"],
                        "exit_pct": (exit_price - pos["entry_price"]) / pos["entry_price"] * 100,
                        "reason": "⏳ 持有期满10日出局",
                        "hold_days": pos["hold_days"]
                    })
                    closed_codes.append(code)
                    n_expired += 1
                    continue

            for code in closed_codes:
                if code in active_positions:
                    del active_positions[code]

        # 4. 计算今日组合市值与今日总资产
        portfolio_value = sum(pos["current_price"] * pos["shares"] for pos in active_positions.values())
        equity = cash + portfolio_value
        current_total_pos_ratio = portfolio_value / equity if equity > 0 else 0.0

        # 5. 买入新仓（受限仓额度限制 + 仓位系数）
        if color != "black":
            today_signals = signals_by_date.get(date, [])
            for code in today_signals:
                # 跳过已持仓股票
                if code in active_positions:
                    continue
                
                # 获取该信号对应的仓位系数
                position_coeff = position_coeff_by_date_code.get((date, code), 1.0)
                adjusted_single_pos_pct = single_pos_pct * position_coeff
                
                # 计算是否允许建新仓：今日持仓市值比例 + 调整后的单股建仓比例 <= 今日总仓位上限
                if current_total_pos_ratio + adjusted_single_pos_pct <= max_total_pos:
                    df_p = price_by_code.get(code)
                    if df_p is None:
                        continue
                    today_row = df_p[df_p["trade_date"] == date]
                    if today_row.empty:
                        continue
                        
                    close_price = float(today_row.iloc[0]["close"])
                    
                    # 按照当前总权益 * 调整后的单股仓位比例买入
                    buy_value = equity * adjusted_single_pos_pct
                    shares = int(buy_value / close_price)
                    
                    if shares > 0 and cash >= shares * close_price:
                        # 扣款
                        cash -= shares * close_price
                        
                        # 计算主止损线
                        low20 = get_low20(code, date)
                        stop_fixed = close_price * (1 - STOP_LOSS_PCT)
                        stop_struct = (low20 * 0.98) if low20 and low20 > 0 else stop_fixed
                        stop_price = max(stop_fixed, stop_struct)
                        
                        active_positions[code] = {
                            "entry_date": date,
                            "entry_price": close_price,
                            "stop_price": stop_price,
                            "shares": shares,
                            "current_price": close_price,
                            "hold_days": 0
                        }
                        # 更新当前仓位占比
                        portfolio_value = sum(pos["current_price"] * pos["shares"] for pos in active_positions.values())
                        current_total_pos_ratio = portfolio_value / equity

        # 再次更新总资产
        portfolio_value = sum(pos["current_price"] * pos["shares"] for pos in active_positions.values())
        equity = cash + portfolio_value
        
        portfolio_equity.append({
            "trade_date": date,
            "equity": equity,
            "cash": cash,
            "portfolio_value": portfolio_value,
            "position_ratio": portfolio_value / equity,
            "color": color
        })

    df_equity = pd.DataFrame(portfolio_equity)
    df_trades = pd.DataFrame(trade_history)
    
    print(f"  回测期末资产: ¥{equity:,.2f} | 止损平仓: {n_stopped} 次 | 满期平仓: {n_expired} 次 | 黑色清仓: {n_black_cleared} 次")
    return df_equity, df_trades


# =============================================================================
# 绩效分析
# =============================================================================
def analyze_performance(df_equity, df_trades):
    if df_equity.empty:
        print("[ERROR] 组合回测结果为空！")
        return None
        
    initial_cap = df_equity.iloc[0]["equity"]
    final_cap   = df_equity.iloc[-1]["equity"]
    
    # 组合层绩效
    total_ret = (final_cap - initial_cap) / initial_cap
    df_equity["daily_ret"] = df_equity["equity"].pct_change().fillna(0)
    ann_ret = (1 + total_ret) ** (252 / len(df_equity)) - 1
    ann_vol = df_equity["daily_ret"].std() * np.sqrt(252)
    sharpe  = (ann_ret - 0.02) / ann_vol if ann_vol > 0 else 0
    
    df_equity["peak"] = df_equity["equity"].cummax()
    df_equity["drawdown"] = (df_equity["equity"] - df_equity["peak"]) / df_equity["peak"]
    max_dd = df_equity["drawdown"].min()

    # 单股交易层绩效
    if not df_trades.empty:
        win_rate = (df_trades["exit_pct"] > 0).mean()
        avg_ret  = df_trades["exit_pct"].mean()
        gains    = df_trades.loc[df_trades["exit_pct"] > 0, "exit_pct"].mean()
        losses   = df_trades.loc[df_trades["exit_pct"] < 0, "exit_pct"].mean()
        pl_ratio = abs(gains / losses) if losses and losses != 0 else float("nan")
        max_loss = df_trades["exit_pct"].min()
        total_trades_count = len(df_trades)
    else:
        win_rate, avg_ret, gains, losses, pl_ratio, max_loss = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        total_trades_count = 0

    print(f"\n{'='*70}")
    print("       StockAI v4.0 - 高胜率因子组合回测报告")
    print(f"{'='*70}")
    print(f"\n【单信号裸交易绩效】")
    print(f"  交易总笔数  : {total_trades_count:,} 笔")
    print(f"  信号平均胜率: {win_rate:.1%}  {'✅' if win_rate >= 0.70 else '⚠️'}")
    print(f"  单次均收益  : {avg_ret:+.2f}%")
    print(f"  平均盈利    : {gains:+.2f}%")
    print(f"  平均亏损    : {losses:+.2f}%")
    print(f"  系统盈亏比  : {pl_ratio:.2f}  {'✅' if pl_ratio >= 1.5 else '⚠️'}")
    print(f"  最大单笔亏损: {max_loss:+.2f}%")

    print(f"\n【限仓组合绩效】")
    print(f"  期末总资产  : ¥{final_cap:,.2f} (初始 ¥{initial_cap:,.2f})")
    print(f"  组合总收益率: {total_ret:+.2%}")
    print(f"  组合年化收益: {ann_ret:+.2%}")
    print(f"  组合最大回撤: {max_dd:.2%}")
    print(f"  组合夏普比率: {sharpe:.3f}")

    return {
        "win_rate": win_rate,
        "pl_ratio": pl_ratio,
        "total_ret": total_ret,
        "max_dd": max_dd,
        "sharpe": sharpe
    }


def calculate_rebound_score(row):
    """
    计算超跌反弹复合评分（0-100）
    维度权重：超跌(40%) + 量能确认(20%) + 均线支撑(20%) + RSI超卖(20%)
    """
    score = 0
    
    # 1. 20日跌幅（权重40%）
    drop_20d = row.get('drop_20d', 0)
    if drop_20d < -0.15:
        score += 40
    elif drop_20d < -0.10:
        score += 30
    elif drop_20d < -0.05:
        score += 15
    
    # 2. 量能确认（权重20%）- 缩量企稳
    vol_ratio_5d = row.get('vol_ratio_5d', 1.0)
    if vol_ratio_5d < 0.8:
        score += 20
    elif vol_ratio_5d < 1.0:
        score += 10
    
    # 3. 均线支撑（权重20%）
    price_ma20_ratio = row.get('price_ma20_ratio', 1.0)
    if price_ma20_ratio < 0.92:
        score += 20
    elif price_ma20_ratio < 0.95:
        score += 10
    
    # 4. RSI超卖（权重20%）
    rsi_14 = row.get('rsi_14', 50)
    if rsi_14 < 30:
        score += 20
    elif rsi_14 < 35:
        score += 10
    
    return score


def main():
    parser = argparse.ArgumentParser(description="高胜率多因子组合回测")
    parser.add_argument("--start",     default="20250601")
    parser.add_argument("--end",       default="20251231")
    parser.add_argument("--composite", action="store_true", help="使用超跌反弹复合评分模式")
    args = parser.parse_args()

    t0 = time.time()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")

    try:
        # 数据加载
        daily_all, money, holder, circ_mv, chips = \
            load_all_data(conn, args.start, args.end)

        # 信号计算
        signals_df = compute_signals(daily_all, money, holder, circ_mv, chips, args.start, conn, args.composite)

        # 交易日历
        trade_dates = sorted(
            daily_all[daily_all["trade_date"] >= args.start]["trade_date"].unique().tolist()
        )

        # 运行组合模拟
        df_equity, df_trades = run_portfolio_simulation(daily_all, signals_df, trade_dates, conn)

        # 绩效分析
        analyze_performance(df_equity, df_trades)

    finally:
        conn.close()

    print(f"\n[DONE] 高胜率组合回测完成，共耗时: {time.time()-t0:.2f} 秒")


if __name__ == "__main__":
    main()

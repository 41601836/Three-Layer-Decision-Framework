# ml_pipeline.py
"""
v4.0 ML 多因子策略每日推理管道
用法：python ml_pipeline.py --trade_date 20260625
"""

import os
import sys
import json
import sqlite3
import pandas as pd
import numpy as np
import joblib
from datetime import datetime, timedelta
from typing import List, Dict

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")
MODEL_PATH = os.path.join(ROOT_DIR, "models", "ensemble_model.pkl")
SCALER_PATH = os.path.join(ROOT_DIR, "models", "factor_scalers.pkl")
INDUSTRY_MEAN_PATH = os.path.join(ROOT_DIR, "models", "industry_means.pkl")

# 与训练时一致的因子列表
FACTORS = ["factor_close_ma20", "factor_vol_ratio", "factor_price_range", "factor_momentum_5d", "factor_momentum_20d", "factor_rsi_10d", "factor_money_intensity", "factor_inflow_days", "factor_pe", "factor_pb", "factor_circ_mv", "factor_holder_trend", "momentum_accel", "main_strength", "chip_delta", "factor_winner_rate", "factor_chip_peak_gap", "factor_sector_resonance"]


def load_model():
    """加载已保存的 Ensemble 模型和标准化参数"""
    model = joblib.load(MODEL_PATH)
    scaler_params = joblib.load(SCALER_PATH)
    industry_means = joblib.load(INDUSTRY_MEAN_PATH)
    return model, scaler_params, industry_means


def build_feature_engineering_inference(daily, money, daily_basic, holder, index_df, chips, hsgt):
    """推理端特征工程构建（与训练脚本 100% 保持一致）"""
    df = daily.sort_values(["ts_code", "trade_date"]).copy()

    # 合并筹码与北向资金
    df = df.merge(chips, on=["ts_code", "trade_date"], how="left")
    df["chips_peak_pct"] = df["chips_peak_pct"].fillna(0.0)
    df["winner_rate"] = df["winner_rate"].fillna(0.5)
    df["peak_price"] = df["peak_price"].fillna(df["close"])

    df = df.merge(hsgt, on=["trade_date"], how="left")
    df["north_money"] = df["north_money"].fillna(0.0)

    # A. 基础日线均线与低位吸筹因子
    df["ma20"] = df.groupby("ts_code")["close"].transform(lambda x: x.rolling(20).mean())
    df["ma60"] = df.groupby("ts_code")["close"].transform(lambda x: x.rolling(60).mean())
    df["vol_ma10"] = df.groupby("ts_code")["vol"].transform(lambda x: x.rolling(10).mean())
    df["vol_ma60"] = df.groupby("ts_code")["vol"].transform(lambda x: x.rolling(60).mean())
    
    df["factor_close_ma20"] = (df["close"] / df["ma20"] - 1).fillna(0)
    df["factor_vol_ratio"] = (df["vol_ma10"] / df["vol_ma60"].replace(0, np.nan)).fillna(1.0)
    df["high_10d"] = df.groupby("ts_code")["high"].transform(lambda x: x.rolling(10).max())
    df["low_10d"] = df.groupby("ts_code")["low"].transform(lambda x: x.rolling(10).min())
    df["factor_price_range"] = ((df["high_10d"] - df["low_10d"]) / df["low_10d"].replace(0, np.nan)).fillna(0.1)

    # ===== 新增特征2：洗盘尾声信号 =====
    # 量能萎缩 + 振幅收窄 + 价格企稳（全部采用 T-1 日的滞后指标）
    df['vol_shift_1'] = df.groupby('ts_code')['vol'].shift(1)
    df['vol_ma20_shift_1'] = df.groupby('ts_code')['vol'].transform(lambda x: x.rolling(20).mean().shift(1))
    df['volume_shrink'] = (df['vol_shift_1'] / df['vol_ma20_shift_1'].replace(0, np.nan) < 0.6).astype(int).fillna(0)
    
    df['amplitude'] = (df['high'] - df['low']) / df['low'].replace(0, np.nan)
    df['amp_ma5_shift_1'] = df.groupby('ts_code')['amplitude'].transform(lambda x: x.rolling(5).mean().shift(1))
    df['amplitude_narrow'] = (df['amp_ma5_shift_1'] < 0.025).astype(int).fillna(0)
    df['washout_ready'] = (df['volume_shrink'] & df['amplitude_narrow']).astype(int)

    # A2. 动量因子 (与价值因子互补，捕捉短中期价格趋势)
    close_5d_ago = df.groupby("ts_code")["close"].transform(lambda x: x.shift(5))
    df["factor_momentum_5d"] = ((df["close"] - close_5d_ago) / close_5d_ago.replace(0, np.nan)).fillna(0)
    close_20d_ago = df.groupby("ts_code")["close"].transform(lambda x: x.shift(20))
    df["factor_momentum_20d"] = ((df["close"] - close_20d_ago) / close_20d_ago.replace(0, np.nan)).fillna(0)
    # 10日 RSI 超卖反弹因子
    delta = df.groupby("ts_code")["close"].transform(lambda x: x.diff())
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.groupby(df["ts_code"]).transform(lambda x: x.rolling(10, min_periods=1).mean())
    avg_loss = loss.groupby(df["ts_code"]).transform(lambda x: x.rolling(10, min_periods=1).mean())
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["factor_rsi_10d"] = (100 - 100 / (1 + rs)).fillna(50)

    # ===== 新增特征6：动量加速度 =====
    # 近3日和近5日动量都必须使用 T-1 的滞后
    df['momentum_3d'] = df.groupby('ts_code')['close'].transform(lambda x: x.pct_change(3).shift(1))
    df['momentum_5d'] = df.groupby('ts_code')['close'].transform(lambda x: x.pct_change(5).shift(1))
    df['momentum_accel'] = (df['momentum_3d'] - df['momentum_5d']).fillna(0.0)

    # B. 资金流向特征
    mdf = money.sort_values(["ts_code", "trade_date"]).copy()
    mdf["net_main_10d_sum"] = mdf.groupby("ts_code")["net_main"].transform(lambda x: x.rolling(10).sum())
    mdf["inflow_day"] = mdf["net_main"] > 0
    mdf["inflow_10d_cnt"] = mdf.groupby("ts_code")["inflow_day"].transform(lambda x: x.rolling(10).sum())
    
    df["amount_10d_sum"] = df.groupby("ts_code")["amount"].transform(lambda x: x.rolling(10).sum())
    
    # 融入主力大单特大单流向
    df = df.merge(mdf[["ts_code", "trade_date", "net_main_10d_sum", "inflow_10d_cnt", "buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount"]], on=["ts_code", "trade_date"], how="left")
    df["net_main_10d_sum"] = df["net_main_10d_sum"].fillna(0)
    df["inflow_10d_cnt"] = df["inflow_10d_cnt"].fillna(0)
    df["buy_elg_amount"] = df["buy_elg_amount"].fillna(0)
    df["sell_elg_amount"] = df["sell_elg_amount"].fillna(0)
    df["buy_lg_amount"] = df["buy_lg_amount"].fillna(0)
    df["sell_lg_amount"] = df["sell_lg_amount"].fillna(0)
    
    df["factor_money_intensity"] = (df["net_main_10d_sum"] / df["amount_10d_sum"].replace(0, np.nan)).fillna(0)
    df["factor_inflow_days"] = df["inflow_10d_cnt"]

    # ===== 新增特征3：主力行为 =====
    df['elg_net_ratio_raw'] = (df['buy_elg_amount'] - df['sell_elg_amount']) / df['amount'].clip(lower=1e6)
    df['big_net_ratio_raw'] = (df['buy_lg_amount'] - df['sell_lg_amount']) / df['amount'].clip(lower=1e6)
    df['elg_net_ratio'] = df.groupby('ts_code')['elg_net_ratio_raw'].shift(1).fillna(0.0)
    df['big_net_ratio'] = df.groupby('ts_code')['big_net_ratio_raw'].shift(1).fillna(0.0)
    df['main_strength'] = df['elg_net_ratio'] + df['big_net_ratio']

    # ===== 新增特征4：北向资金趋势 =====
    df['north_5d'] = df.groupby('ts_code')['north_money'].transform(lambda x: x.rolling(5).sum())
    df['north_trend_raw'] = df.groupby('ts_code')['north_5d'].transform(lambda x: (x > x.shift(5)).astype(int))
    df['north_trend'] = df.groupby('ts_code')['north_trend_raw'].shift(1).fillna(0)

    # C. 每日基本指标与估值特征
    df = df.merge(daily_basic[["ts_code", "trade_date", "pe", "pb", "circ_mv"]], on=["ts_code", "trade_date"], how="left")
    df["pe"] = pd.to_numeric(df["pe"]).fillna(30.0)
    df["pb"] = pd.to_numeric(df["pb"]).fillna(2.0)
    df["circ_mv"] = pd.to_numeric(df["circ_mv"]).fillna(500000.0)
    
    df["factor_pe"] = np.log1p(df["pe"].clip(lower=1))
    df["factor_pb"] = np.log1p(df["pb"].clip(lower=0.1))
    df["factor_circ_mv"] = np.log1p(df["circ_mv"])

    # D. 股东人数变化 (使用 pd.merge_asof 极速对齐)
    print("[ML] 正在匹配每日可见的股东人数变动趋势...")
    holder_sorted = holder.sort_values(["ts_code", "end_date"]).copy()
    holder_sorted["prev_holder_num"] = holder_sorted.groupby("ts_code")["holder_num"].shift(1)
    holder_sorted["holder_change"] = ((holder_sorted["holder_num"] - holder_sorted["prev_holder_num"]) / holder_sorted["prev_holder_num"].replace(0, np.nan)).fillna(0.0)
    
    df_sorted = df.sort_values("trade_date").copy()
    holder_to_merge = holder_sorted[["ts_code", "ann_date", "holder_change"]].sort_values("ann_date").copy()
    
    df_sorted["trade_date_int"] = pd.to_numeric(df_sorted["trade_date"])
    holder_to_merge["ann_date_int"] = pd.to_numeric(holder_to_merge["ann_date"])
    
    df_merged = pd.merge_asof(
        df_sorted,
        holder_to_merge,
        left_on="trade_date_int",
        right_on="ann_date_int",
        by="ts_code",
        direction="backward"
    )
    
    df_merged["factor_holder_trend"] = df_merged["holder_change"].fillna(0.0)
    if "ann_date_y" in df_merged.columns:
        df_merged = df_merged.drop(columns=["ann_date_y"])
    if "ann_date_x" in df_merged.columns:
        df_merged.rename(columns={"ann_date_x": "ann_date"}, inplace=True)
        
    df = df_merged.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    # ===== 新增特征5：筹码集中度变化 =====
    df['chip_peak'] = df['chips_peak_pct']
    df['chip_delta'] = df.groupby('ts_code')['chip_peak'].transform(lambda x: x.shift(1) - x.shift(6)).fillna(0.0)

    # ===== 新增特征：筹码特征与板块共振 =====
    df["factor_winner_rate"] = df["winner_rate"]
    df["factor_chip_peak_gap"] = ((df["close"] - df["peak_price"]) / df["peak_price"].replace(0, np.nan)).fillna(0.0)
    
    sector_avg = df.groupby(["trade_date", "industry"])["pct_chg"].transform("mean")
    df["factor_sector_resonance"] = (df["pct_chg"] - sector_avg).fillna(0.0)

    # 计算 20 日和 5 日价格变化（供衍生左右侧信号使用，注意：在推理评分后这二者用的是实时 close）
    df['pct_change_20'] = df.groupby('ts_code')['close'].transform(lambda x: x.pct_change(20))
    df['pct_change_5'] = df.groupby('ts_code')['close'].transform(lambda x: x.pct_change(5))

    return df




def fetch_today_factors(conn, trade_date):
    """拉取 trade_date 之前 80 个交易日的历史数据，通过 build_feature_engineering 构建镜像一致的因子特征"""
    print(f"[ML] 开始拉取 {trade_date} 之前 80 个交易日的时序数据...")
    dates_df = pd.read_sql(f"""
        SELECT DISTINCT trade_date FROM daily_prices 
        WHERE trade_date <= '{trade_date}' 
        ORDER BY trade_date DESC LIMIT 80
    """, conn)
    if dates_df.empty:
        return pd.DataFrame()
    dates = sorted(dates_df["trade_date"].tolist())
    
    placeholders = ",".join(["?"] * len(dates))

    daily = pd.read_sql(f"""
        SELECT dp.ts_code, dp.trade_date, dp.open, dp.high, dp.low, dp.close, dp.vol, dp.amount, sl.industry, sl.name
        FROM daily_prices dp
        LEFT JOIN stock_list sl ON dp.ts_code = sl.ts_code
        WHERE dp.trade_date IN ({placeholders})
          AND dp.close > 0
          AND sl.industry IS NOT NULL
          AND sl.name NOT LIKE '%ST%'
    """, conn, params=dates)

    if daily.empty:
        return pd.DataFrame()

    money = pd.read_sql(f"""
        SELECT ts_code, trade_date, buy_elg_amount, sell_elg_amount, buy_lg_amount, sell_lg_amount
        FROM moneyflow
        WHERE trade_date IN ({placeholders})
    """, conn, params=dates)
    for col in ["buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount"]:
        money[col] = pd.to_numeric(money[col]).fillna(0)
    money["net_main"] = (money["buy_elg_amount"] + money["buy_lg_amount"] -
                         money["sell_elg_amount"] - money["sell_lg_amount"])

    daily_basic = pd.read_sql(f"""
        SELECT ts_code, trade_date, pe, pb, circ_mv
        FROM daily_basic
        WHERE trade_date IN ({placeholders})
    """, conn, params=dates)

    holder = pd.read_sql("""
        SELECT ts_code, ann_date, end_date, holder_num
        FROM stk_holdernumber
    """, conn)
    holder["holder_num"] = pd.to_numeric(holder["holder_num"]).fillna(0)
    holder["ann_date"] = holder["ann_date"].fillna(holder["end_date"])

    index_df = pd.read_sql(f"""
        SELECT trade_date, close as index_close
        FROM daily_index
        WHERE ts_code = '000001.SH' AND trade_date IN ({placeholders})
    """, conn, params=dates)

    chips = pd.read_sql(f"""
        WITH RankedChips AS (
            SELECT 
                ts_code, 
                trade_date, 
                price as peak_price,
                percent as chips_peak_pct,
                ROW_NUMBER() OVER (PARTITION BY ts_code, trade_date ORDER BY percent DESC, price ASC) as rn
            FROM cyq_chips
            WHERE trade_date IN ({placeholders})
        ),
        WinnerChips AS (
            SELECT 
                c.ts_code, 
                c.trade_date,
                SUM(CASE WHEN c.price <= dp.close THEN c.percent ELSE 0 END) / 100.0 as winner_rate
            FROM cyq_chips c
            JOIN daily_prices dp ON c.ts_code = dp.ts_code AND c.trade_date = dp.trade_date
            WHERE c.trade_date IN ({placeholders})
            GROUP BY c.ts_code, c.trade_date
        )
        SELECT 
            wc.ts_code,
            wc.trade_date,
            wc.winner_rate,
            rc.peak_price,
            rc.chips_peak_pct
        FROM WinnerChips wc
        JOIN RankedChips rc ON wc.ts_code = rc.ts_code AND wc.trade_date = rc.trade_date AND rc.rn = 1
    """, conn, params=dates + dates)

    hsgt = pd.read_sql(f"""
        SELECT trade_date, north_money
        FROM hsgt_moneyflow
        WHERE trade_date IN ({placeholders})
    """, conn, params=dates)

    # 时序特征工程计算
    df = build_feature_engineering_inference(daily, money, daily_basic, holder, index_df, chips, hsgt)
    
    # 截取当日数据并返回
    df_today = df[df["trade_date"] == trade_date].copy()
    return df_today


def neutralize_factors(df, scaler_params, industry_means, factors):
    """
    行业中性化 + MAD 去极值 + Z-Score（与训练时 100% 对齐）
    """
    # 1. 行业去均值（使用训练集的行业均值）
    for f in factors:
        df[f"{f}_neutral"] = df.apply(
            lambda row: row[f] - industry_means.get(f, {}).get(row['industry'], 0),
            axis=1
        ).fillna(0)
    
    # 2. 对中性化后的因子做 MAD 去极值与 Z-Score
    for f in factors:
        series = df[f"{f}_neutral"]
        median = series.median()
        mad = (series - median).abs().median()
        if mad > 0:
            limit = 3.0 * 1.4826 * mad
            series = series.clip(lower=median - limit, upper=median + limit)
            
        mean = scaler_params.get(f"mean_{f}", 0)
        std = scaler_params.get(f"std_{f}", 1)
        if std == 0:
            std = 1
        df[f] = (df[f"{f}_neutral"] - mean) / std
    
    # 处理可能产生的 NaN 值
    df = df.fillna(0)
    
    return df


def get_ma5_filter(conn, trade_date, codes):
    """获取 MA5 过滤后的股票列表（股价 > MA5）"""
    if not codes:
        return []
    placeholders = ",".join(["?"] * len(codes))
    df = pd.read_sql(f"""
        SELECT ts_code, close,
               AVG(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS ma5
        FROM daily_prices
        WHERE ts_code IN ({placeholders})
          AND trade_date <= '{trade_date}'
        ORDER BY ts_code, trade_date DESC
    """, conn, params=codes)
    # 取每个股票最新一条
    df = df.groupby("ts_code").first().reset_index()
    return df[df["close"] > df["ma5"]]["ts_code"].tolist()


def get_industry_filter(conn, trade_date, top_n=3):
    """获取当日行业强度排名前 N 的行业列表（复用 industry_strength 逻辑）"""
    try:
        from industry_strength import calc_industry_strength
        df = calc_industry_strength(conn, target_date=trade_date)
        if not df.empty:
            return df.head(top_n)["industry"].tolist()
    except Exception:
        # 降级方案：用行业平均涨幅简单排序
        df = pd.read_sql(f"""
            SELECT sl.industry, AVG(dp.pct_chg) as avg_chg
            FROM daily_prices dp
            JOIN stock_list sl ON dp.ts_code = sl.ts_code
            WHERE dp.trade_date = '{trade_date}'
              AND sl.industry IS NOT NULL
            GROUP BY sl.industry
            ORDER BY avg_chg DESC
            LIMIT {top_n}
        """, conn)
        return df["industry"].tolist() if not df.empty else []
    return []


def filter_core_stocks(candidates_df, conn, trade_date, core_n=10):
    """核心筛选：得分优先 + 适度风控（路径一：保留冷门股）"""
    df = candidates_df.copy()
    
    # ① 得分必须 > 0（正超额预期）
    df = df[df['ml_score'] > 0]
    
    if df.empty:
        return []
    
    # ② 得分排名前 50%（避免只取头部，给冷门股空间）
    df = df[df['ml_score'] >= df['ml_score'].quantile(0.50)]
    
    if df.empty:
        return []
    
    # ③ 行业强度：不过滤，只记录排名（给冷门股机会）
    try:
        from industry_strength import calc_industry_strength
        industry_df = calc_industry_strength(conn, target_date=trade_date)
    except Exception:
        industry_df = pd.read_sql(f"""
            SELECT sl.industry, AVG(dp.pct_chg) as avg_chg
            FROM daily_prices dp
            JOIN stock_list sl ON dp.ts_code = sl.ts_code
            WHERE dp.trade_date = '{trade_date}'
              AND sl.industry IS NOT NULL
            GROUP BY sl.industry
            ORDER BY avg_chg DESC
        """, conn)
    
    industry_rank = {row['industry']: i+1 for i, row in industry_df.iterrows()}
    
    # 为每条记录添加行业强度排名，但不过滤
    df['industry_rank'] = df['industry'].map(industry_rank).fillna(999)
    
    # ④ 技术面：放宽条件（不要求MA5，只要求不处于极端下跌）
    # 近5日跌幅 > -15%（允许冷门股下跌，但不崩盘）
    codes = df['ts_code'].tolist()
    placeholders = ",".join(['?'] * len(codes))
    tech_df = pd.read_sql(f"""
        SELECT ts_code, 
               (close - LAG(close, 5) OVER (PARTITION BY ts_code ORDER BY trade_date)) / LAG(close, 5) OVER (PARTITION BY ts_code ORDER BY trade_date) AS ret_5d,
               AVG(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS ma5
        FROM daily_prices
        WHERE ts_code IN ({placeholders})
          AND trade_date <= '{trade_date}'
        ORDER BY ts_code, trade_date DESC
    """, conn, params=codes)
    tech_df = tech_df.groupby('ts_code').first().reset_index()
    df = df.merge(tech_df[['ts_code', 'ret_5d', 'ma5']], on='ts_code', how='left')
    
    # 移除 MA5 过滤，只检查不极端下跌
    df = df[(df['ret_5d'] > -0.15) | df['ret_5d'].isna()]
    
    if df.empty:
        return []
    
    # ⑤ 流动性：放宽到 1000万（冷门股也能通过）
    amount_df = pd.read_sql(f"""
        SELECT ts_code, AVG(amount) / 10000 AS avg_amount_wan
        FROM daily_prices
        WHERE ts_code IN ({placeholders})
          AND trade_date >= date('{trade_date}', '-20 days')
        GROUP BY ts_code
    """, conn, params=codes)
    df = df.merge(amount_df, on='ts_code', how='left')
    df = df[(df['avg_amount_wan'] >= 1000) | df['avg_amount_wan'].isna()]
    
    if df.empty:
        return []
    
    # ⑥ 回撤：放宽到 30%（允许冷门股处于低位）
    low20_df = pd.read_sql(f"""
        SELECT ts_code, MIN(low) AS low_20d
        FROM daily_prices
        WHERE ts_code IN ({placeholders})
          AND trade_date >= date('{trade_date}', '-20 days')
        GROUP BY ts_code
    """, conn, params=codes)
    df = df.merge(low20_df, on='ts_code', how='left')
    df['drawdown'] = (df['close'] - df['low_20d']) / df['low_20d']
    df = df[(df['drawdown'] < 0.30) | df['drawdown'].isna()]
    
    # 排序取前 N
    df = df.sort_values('ml_score', ascending=False).head(core_n)
    return df.to_dict('records')


def run_ml_pipeline(trade_date: str = None, top_pct: float = 0.10, require_ma5: bool = True, require_industry: bool = True):
    """执行 v4.0 完整推理管道"""
    if not trade_date:
        trade_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    print(f"🚀 运行 v4.0 ML 推理 | 日期: {trade_date}")

    conn = sqlite3.connect(DB_PATH)

    # 1. 加载模型与参数
    model, scaler_params, industry_means = load_model()
    factors = [k[5:] for k in scaler_params.keys() if k.startswith("mean_")]

    # 2. 获取当日因子数据
    df = fetch_today_factors(conn, trade_date)
    if df.empty:
        print("⚠️ 当日无数据")
        conn.close()
        return []

    # 3. 行业中性化
    df = neutralize_factors(df, scaler_params, industry_means, factors)

    # 4. 预测得分 (Ensemble分类器概率集成)
    X = df[factors].values
    df["ml_score"] = model.predict_proba(X)

    # 衍生计算左右侧信号
    df["left_signal"] = ((df["pct_change_20"] < -0.05) & (df["ml_score"] > 0.6)).astype(int)
    df["right_signal"] = ((df["pct_change_5"] > 0.0) & (df["ml_score"] > 0.7)).astype(int)

    # 5. 筛选 Top N%
    threshold = df["ml_score"].quantile(1 - top_pct)
    candidates = df[df["ml_score"] >= threshold].copy()

    # 7. MA5 企稳过滤（如果开启）
    if require_ma5:
        valid_codes = get_ma5_filter(conn, trade_date, candidates["ts_code"].tolist())
        candidates = candidates[candidates["ts_code"].isin(valid_codes)]

    # 8. 行业强度过滤（如果开启）
    if require_industry:
        strong_industries = get_industry_filter(conn, trade_date, top_n=3)
        if strong_industries:
            candidates = candidates[candidates["industry"].isin(strong_industries)]

    # 9. 按得分排序
    candidates = candidates.sort_values("ml_score", ascending=False)
    
    # 10. 计算个股在板块中的排名
    candidates['sector_rank'] = candidates.groupby('industry')['ml_score'].rank(ascending=False, method='min').astype(int)
    
    # 11. 筛选核心建仓标的
    core_stocks = filter_core_stocks(candidates, conn, trade_date, core_n=20)

    # 12. 组装输出
    result = []
    for _, row in candidates.iterrows():
        result.append({
            "ts_code": row["ts_code"],
            "name": row.get("name", ""),
            "industry": row["industry"],
            "sector_rank": int(row["sector_rank"]),
            "score": float(row["ml_score"]),
            "close": float(row["close"]),
            "pct_chg": float(row["pct_chg"]),
            "left_signal": int(row["left_signal"]),
            "right_signal": int(row["right_signal"])
        })
    
    # 组装核心标的输出（包含辅助决策信息）
    core_result = []
    for stock in core_stocks:
        avg_amount = stock.get("avg_amount_wan", 0)
        industry_rank = stock.get("industry_rank", 999)
        core_result.append({
            "ts_code": stock["ts_code"],
            "name": stock.get("name", ""),
            "industry": stock["industry"],
            "industry_rank": f"第{industry_rank}名" if industry_rank <= 50 else "未上榜",
            "score": float(stock["ml_score"]),
            "close": float(stock["close"]),
            "pct_chg": float(stock["pct_chg"]),
            "ret_5d": float(stock.get("ret_5d", 0)) * 100,
            "ma5_position": "上方" if stock["close"] > stock["ma5"] else "下方",
            "drawdown_20d": float(stock.get("drawdown", 0)) * 100,
            "avg_amount_wan": int(avg_amount) if not pd.isna(avg_amount) else 0,
            "left_signal": int(stock["left_signal"]),
            "right_signal": int(stock["right_signal"])
        })

    # 保存到缓存文件（包含完整列表和核心标的）
    cache_file = os.path.join(ROOT_DIR, "last_ml_candidates.json")
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump({
            "candidates": result,
            "core_stocks": core_result
        }, f, ensure_ascii=False, indent=2)

    print(f"✅ 筛选出 {len(result)} 只候选股")
    print(f"🎯 核心建仓标的: {len(core_result)} 只")
    conn.close()
    return result



if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--trade_date", type=str, help="交易日期 YYYYMMDD")
    parser.add_argument("--top_pct", type=float, default=0.10)
    parser.add_argument("--no_ma5", action="store_true")
    parser.add_argument("--no_industry", action="store_true")
    args = parser.parse_args()

    candidates = run_ml_pipeline(
        trade_date=args.trade_date,
        top_pct=args.top_pct,
        require_ma5=not args.no_ma5,
        require_industry=not args.no_industry
    )
    print(json.dumps(candidates, indent=2, ensure_ascii=False))
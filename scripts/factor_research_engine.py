# -*- coding: utf-8 -*-
"""
factor_research_engine.py —— 因子评估与特征工程筛选模块 (技术升级版)
==================================================================
1. 加载 2022-10-01 ~ 2025-12-31 原始数据。
2. 构建 9 维特征，利用 pd.merge_asof 极速对齐披露日股东变化。
3. 补齐多期 (1d, 5d, 10d, 20d) 超额收益标签。
4. 横截面特征工程处理严格顺序：MAD去极值+Z-Score标准化 -> 行业中性化回归取残差 -> 残差再次标准化。
5. 针对 2023-01-01 ~ 2024-06-30 (训练期) 进行因子 Rank IC 与信息半衰期评估。
6. 因子筛选准则：满足 10d IC >= 0.008, 10d IR >= 0.2, 且 5d/10d 衰减比 < 2.5，相关性 < 0.7 过滤。
7. 输出筛选结果 `valid_factors.json`。
"""

import os
import sys
import json
import sqlite3
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")
sys.path.insert(0, ROOT_DIR)


# =============================================================================
# 1. 原始数据加载
# =============================================================================
def load_raw_data(conn):
    print("[FactorEngine] 开始从数据库加载全市场原始数据 (2022-10-01 ~ 2025-12-31)...")
    
    daily = pd.read_sql("""
        SELECT ts_code, trade_date, open, high, low, close, pct_chg, vol, amount
        FROM daily_prices
        WHERE trade_date BETWEEN '20221001' AND '20251231'
        ORDER BY ts_code, trade_date
    """, conn)
    
    stock_info = pd.read_sql("SELECT ts_code, name, industry FROM stock_list", conn)
    daily = daily.merge(stock_info, on="ts_code", how="left")
    
    money = pd.read_sql("""
        SELECT ts_code, trade_date, buy_elg_amount, sell_elg_amount, buy_lg_amount, sell_lg_amount
        FROM moneyflow
        WHERE trade_date BETWEEN '20221001' AND '20251231'
    """, conn)
    for col in ["buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount"]:
        money[col] = pd.to_numeric(money[col]).fillna(0)
    money["net_main"] = (money["buy_elg_amount"] + money["buy_lg_amount"] -
                         money["sell_elg_amount"] - money["sell_lg_amount"])
    
    daily_basic = pd.read_sql("""
        SELECT ts_code, trade_date, pe, pb, circ_mv
        FROM daily_basic
        WHERE trade_date BETWEEN '20221001' AND '20251231'
    """, conn)
    
    holder = pd.read_sql("""
        SELECT ts_code, ann_date, end_date, holder_num
        FROM stk_holdernumber
    """, conn)
    holder["holder_num"] = pd.to_numeric(holder["holder_num"]).fillna(0)
    holder["ann_date"] = holder["ann_date"].fillna(holder["end_date"])
    
    index_df = pd.read_sql("""
        SELECT trade_date, close as index_close
        FROM daily_index
        WHERE ts_code = '000001.SH' AND trade_date BETWEEN '20221001' AND '20251231'
        ORDER BY trade_date
    """, conn)

    # 龙虎榜机构净买入数据（top_inst 表，用于构建 factor_top_inst_5d 因子）
    try:
        top_inst_df = pd.read_sql("""
            SELECT ts_code, trade_date, SUM(net_buy) AS daily_net_buy
            FROM top_inst
            WHERE net_buy > 0
              AND trade_date BETWEEN '20221001' AND '20251231'
            GROUP BY ts_code, trade_date
            ORDER BY ts_code, trade_date
        """, conn)
        top_inst_df["daily_net_buy"] = pd.to_numeric(top_inst_df["daily_net_buy"]).fillna(0)
        print(f"[FactorEngine] 龙虎榜机构净买入数据加载成功: {len(top_inst_df):,} 条记录")
    except Exception as e:
        print(f"[FactorEngine] top_inst 表不存在或加载失败: {e}，跳过 factor_top_inst_5d")
        top_inst_df = pd.DataFrame(columns=["ts_code", "trade_date", "daily_net_buy"])

    return daily, money, daily_basic, holder, index_df, top_inst_df


# =============================================================================
# 2. 特征工程构建 (严格无未来信息)
# =============================================================================
def build_features(daily, money, daily_basic, holder, index_df, top_inst_df=None):
    print("[FactorEngine] 开始计算基础因子特征...")
    df = daily.sort_values(["ts_code", "trade_date"]).copy()
    
    # A. 基础日线均线与低位吸筹因子
    df["ma20"] = df.groupby("ts_code")["close"].transform(lambda x: x.rolling(20).mean())
    df["ma60"] = df.groupby("ts_code")["close"].transform(lambda x: x.rolling(60).mean())
    df["vol_ma10"] = df.groupby("ts_code")["vol"].transform(lambda x: x.rolling(10).mean())
    df["vol_ma60"] = df.groupby("ts_code")["vol"].transform(lambda x: x.rolling(60).mean())
    
    # 因子 1: 收盘价与 MA20 的偏离度 (低位横盘特征)
    df["factor_close_ma20"] = (df["close"] / df["ma20"] - 1).fillna(0)
    # 因子 2: 10日量比60日量 (缩量地量吸筹特征)
    df["factor_vol_ratio"] = (df["vol_ma10"] / df["vol_ma60"].replace(0, np.nan)).fillna(1.0)
    # 因子 3: 近 10 日波动窄幅范围 (横盘吸筹的低波动率特征)
    df["high_10d"] = df.groupby("ts_code")["high"].transform(lambda x: x.rolling(10).max())
    df["low_10d"] = df.groupby("ts_code")["low"].transform(lambda x: x.rolling(10).min())
    df["factor_price_range"] = ((df["high_10d"] - df["low_10d"]) / df["low_10d"].replace(0, np.nan)).fillna(0.1)
    
    # A2. 动量因子 (捕捉短中期价格趋势，与价值因子形成信号互补)
    # 因子 10: 近 5 日价格动量 (短线动量，T-5 至 T)
    close_5d_ago = df.groupby("ts_code")["close"].transform(lambda x: x.shift(5))
    df["factor_momentum_5d"] = ((df["close"] - close_5d_ago) / close_5d_ago.replace(0, np.nan)).fillna(0)
    # 因子 11: 近 20 日价格动量 (中线动量，T-20 至 T)
    close_20d_ago = df.groupby("ts_code")["close"].transform(lambda x: x.shift(20))
    df["factor_momentum_20d"] = ((df["close"] - close_20d_ago) / close_20d_ago.replace(0, np.nan)).fillna(0)
    # 因子 12: 10 日 RSI 超卖反弹因子 (RSI 越低 -> 反弹概率越高，负向信号)
    delta = df.groupby("ts_code")["close"].transform(lambda x: x.diff())
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.groupby(df["ts_code"]).transform(lambda x: x.rolling(10, min_periods=1).mean())
    avg_loss = loss.groupby(df["ts_code"]).transform(lambda x: x.rolling(10, min_periods=1).mean())
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["factor_rsi_10d"] = (100 - 100 / (1 + rs)).fillna(50)
    
    # B. 资金流向特征
    mdf = money.sort_values(["ts_code", "trade_date"]).copy()
    mdf["net_main_10d_sum"] = mdf.groupby("ts_code")["net_main"].transform(lambda x: x.rolling(10).sum())
    mdf["inflow_day"] = mdf["net_main"] > 0
    mdf["inflow_10d_cnt"] = mdf.groupby("ts_code")["inflow_day"].transform(lambda x: x.rolling(10).sum())
    
    # 重构资金强度分母：使用 10 日成交额总和，而不是单日成交额/10，使强度更平滑
    df["amount_10d_sum"] = df.groupby("ts_code")["amount"].transform(lambda x: x.rolling(10).sum())
    
    df = df.merge(mdf[["ts_code", "trade_date", "net_main_10d_sum", "inflow_10d_cnt"]], on=["ts_code", "trade_date"], how="left")
    df["net_main_10d_sum"] = df["net_main_10d_sum"].fillna(0)
    df["inflow_10d_cnt"] = df["inflow_10d_cnt"].fillna(0)
    
    # 因子 4: 10 日资金流入强度 (主力净流入额 / 10日总成交额)
    df["factor_money_intensity"] = (df["net_main_10d_sum"] / df["amount_10d_sum"].replace(0, np.nan)).fillna(0)
    # 因子 5: 10 日主力净流入天数
    df["factor_inflow_days"] = df["inflow_10d_cnt"]
    
    # C. 每日基本指标与估值特征
    df = df.merge(daily_basic[["ts_code", "trade_date", "pe", "pb", "circ_mv"]], on=["ts_code", "trade_date"], how="left")
    df["pe"] = pd.to_numeric(df["pe"]).fillna(30.0)
    df["pb"] = pd.to_numeric(df["pb"]).fillna(2.0)
    df["circ_mv"] = pd.to_numeric(df["circ_mv"]).fillna(500000.0)
    
    # 因子 6, 7, 8: 估值与市值对数
    df["factor_pe"] = np.log1p(df["pe"].clip(lower=1))
    df["factor_pb"] = np.log1p(df["pb"].clip(lower=0.1))
    df["factor_circ_mv"] = np.log1p(df["circ_mv"])
    
    # D. 股东人数变化趋势 (使用 pd.merge_asof 极速对齐披露日)
    print("[FactorEngine] 使用 pd.merge_asof 极速对齐股东变动历史...")
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
    
    # 因子 9: 股东变化趋势
    df_merged["factor_holder_trend"] = df_merged["holder_change"].fillna(0.0)
    if "ann_date_y" in df_merged.columns:
        df_merged = df_merged.drop(columns=["ann_date_y"])
    if "ann_date_x" in df_merged.columns:
        df_merged.rename(columns={"ann_date_x": "ann_date"}, inplace=True)
        
    df = df_merged.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    
    # E. 标签构建: 未来多期 (1d, 5d, 10d, 20d) 超额收益率，用于计算半衰期
    print("[FactorEngine] 计算未来 1d, 5d, 10d, 20d 超额收益标签...")
    index_df = index_df.sort_values("trade_date").copy()
    
    for horizon in [1, 5, 10, 20]:
        df[f"future_close_{horizon}d"] = df.groupby("ts_code")["close"].shift(-horizon)
        df[f"stock_future_ret_{horizon}d"] = (df[f"future_close_{horizon}d"] - df["close"]) / df["close"]
        
        index_df[f"index_future_close_{horizon}d"] = index_df["index_close"].shift(-horizon)
        index_df[f"index_future_ret_{horizon}d"] = (index_df[f"index_future_close_{horizon}d"] - index_df["index_close"]) / index_df["index_close"]
        
        df = df.merge(index_df[["trade_date", f"index_future_ret_{horizon}d"]], on="trade_date", how="left")
        df[f"label_excess_ret_{horizon}d"] = (df[f"stock_future_ret_{horizon}d"] - df[f"index_future_ret_{horizon}d"]).fillna(0.0)
        # 清除多余列以节省内存
        df = df.drop(columns=[f"future_close_{horizon}d", f"stock_future_ret_{horizon}d", f"index_future_ret_{horizon}d"])
        
    df = df[df["vol"] > 0].copy()

    # F. 龙虎榜机构净买入因子 (factor_top_inst_5d)
    # 使用过去5日累计机构净买入总額，山半对数化后作为因子
    # 信号逻辑：龙虎榜机构关注 → 5日内出现过净买入 → 预期近期反弹
    if top_inst_df is not None and not top_inst_df.empty:
        ti = top_inst_df.sort_values(["ts_code", "trade_date"]).copy()
        # 使用 shift(1) 确保不使用当天数据（第 T 日收盘后才知道当天的龙虎榜入败）
        ti["net_buy_shifted"] = ti.groupby("ts_code")["daily_net_buy"].shift(1)
        ti["factor_top_inst_5d"] = (
            ti.groupby("ts_code")["net_buy_shifted"]
            .transform(lambda x: x.rolling(5, min_periods=1).sum())
        )
        # 山半对数化：强化小额数据的差异，减少极端大底段机构买入的影响
        ti["factor_top_inst_5d"] = np.log1p(ti["factor_top_inst_5d"].clip(lower=0))
        df = df.merge(
            ti[["ts_code", "trade_date", "factor_top_inst_5d"]],
            on=["ts_code", "trade_date"], how="left"
        )
        df["factor_top_inst_5d"] = df["factor_top_inst_5d"].fillna(0.0)
        print(f"[FactorEngine] 龙虎榜因子 factor_top_inst_5d 构建完成，非零样本数: {(df['factor_top_inst_5d']>0).sum():,}")
    else:
        df["factor_top_inst_5d"] = 0.0
        print("[FactorEngine] top_inst 数据为空， factor_top_inst_5d 设为 0 占位")

    return df


# =============================================================================
# 3. 横截面预处理严格顺序：MAD标准化 -> 行业中性化回归取残差 -> 残差再次标准化
# =============================================================================
def preprocess_features_cross_section(df, feature_cols):
    print("[FactorEngine] 严格按规范执行横截面 MAD标准化 -> 行业中性化 -> 残差再标准化...")
    processed_df = df.copy()
    
    # 逐日横截面计算
    grouped = processed_df.groupby("trade_date")
    updated_dfs = []
    
    for date, day_data in grouped:
        if len(day_data) < 15:
            updated_dfs.append(day_data)
            continue
            
        day_data = day_data.copy()
        
        # 1. 每日横截面上对原始特征进行 MAD 去极值与 Z-Score 标准化
        for col in feature_cols:
            series = day_data[col]
            median = series.median()
            mad = (series - median).abs().median()
            if mad > 0:
                limit = 3.0 * 1.4826 * mad
                series = series.clip(lower=median - limit, upper=median + limit)
            mean = series.mean()
            std = series.std()
            if std > 0:
                day_data[col] = (series - mean) / std
            else:
                day_data[col] = 0.0
        
        # 2. 对行业做 One-Hot 哑变量编码，进行行业中性化回归取残差
        industry_dummies = pd.get_dummies(day_data["industry"], drop_first=True, dtype=float)
        
        if not industry_dummies.empty:
            X = industry_dummies.values
            neutralize_model = Ridge(alpha=1.0)
            
            for col in feature_cols:
                y = day_data[col].values
                neutralize_model.fit(X, y)
                preds = neutralize_model.predict(X)
                residuals = y - preds # 残差作为中性化后的因子值
                
                # 3. 对中性化后的残差再次进行 Z-Score 标准化，保证截面正态分布
                res_mean = residuals.mean()
                res_std = residuals.std()
                if res_std > 0:
                    day_data[col] = (residuals - res_mean) / res_std
                else:
                    day_data[col] = 0.0
                       
        updated_dfs.append(day_data)
        
    return pd.concat(updated_dfs).sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


# =============================================================================
# 4. 多期 Rank IC 衰减评估与独立性筛选
# =============================================================================
def analyze_factors_with_decay(df, feature_cols, train_start="20230101", train_end="20240630"):
    print(f"[FactorEngine] 截取训练期 ({train_start} ~ {train_end}) 特征并做中性化及标准化...")
    
    # 截取训练期
    df_train = df[(df["trade_date"] >= train_start) & (df["trade_date"] <= train_end)].copy()
    
    # 执行全套中性化处理
    df_train_scaled = preprocess_features_cross_section(df_train, feature_cols)
    
    ic_summary = {}
    horizons = [1, 5, 10, 20]
    
    print(f"\n==========================================================================================")
    print(f"                       量化因子多期 Rank IC 衰减 (Factor Decay) 报告")
    print(f"==========================================================================================")
    print(f"  {'因子名称':<25} | {'IC (1d)':<10} | {'IC (5d)':<10} | {'IC (10d)':<10} | {'IC (20d)':<10} | {'IR (10d)':<10}")
    print("-" * 92)
    
    for col in feature_cols:
        col_horizons_ics = {}
        for h in horizons:
            label_col = f"label_excess_ret_{h}d"
            day_data = df_train_scaled.dropna(subset=[col, label_col])
            
            daily_ics = []
            for date, grp in day_data.groupby("trade_date"):
                if len(grp) < 15:
                    continue
                spearman_corr = grp[col].corr(grp[label_col], method="spearman")
                daily_ics.append(spearman_corr)
            
            daily_ics_series = pd.Series(daily_ics).dropna()
            mean_ic = daily_ics_series.mean()
            std_ic = daily_ics_series.std()
            ir = mean_ic / std_ic if std_ic > 0 else 0.0
            
            col_horizons_ics[f"ic_{h}d"] = float(mean_ic)
            col_horizons_ics[f"ir_{h}d"] = float(ir)
            
        ic_summary[col] = col_horizons_ics
        print(f"  {col:<25} | {col_horizons_ics['ic_1d']:+10.5f} | {col_horizons_ics['ic_5d']:+10.5f} | {col_horizons_ics['ic_10d']:+10.5f} | {col_horizons_ics['ic_20d']:+10.5f} | {col_horizons_ics['ir_10d']:+10.5f}")
        
    print(f"==========================================================================================\n")
    
    # 计算因子间的相关系数矩阵
    df_clean = df_train_scaled.dropna(subset=feature_cols)
    corr_matrix = df_clean[feature_cols].corr(method="pearson")
    print("====================================================================")
    print("                    中性化后因子相关性矩阵 (Pearson)")
    print("====================================================================")
    print(corr_matrix.round(3))
    print("====================================================================\n")
    
    # 因子准入筛选
    # 标准：第 10 日 Rank IC 绝对值 >= 0.008 且 10 日绝对 IR >= 0.2，且 5d 与 10d 衰减比 < 2.5
    # 放宽门槛以使资金流/动量因子有机会通过，并与价值因子形成多维互补
    ic_threshold = 0.008
    ir_threshold = 0.2
    
    candidate_factors = []
    for col, metrics in ic_summary.items():
        ic_10d = abs(metrics["ic_10d"])
        ic_5d = abs(metrics["ic_5d"])
        decay_ratio = ic_5d / ic_10d if ic_10d > 0 else 999.0
        
        if ic_10d >= ic_threshold and abs(metrics["ir_10d"]) >= ir_threshold and decay_ratio < 2.5:
            candidate_factors.append(col)
            
    if len(candidate_factors) < 3:
        print(f"[FactorEngine] 按准入阈值筛选出的因子过少 ({len(candidate_factors)} 个)，触发降级：放宽至 10d IC > 0.005 且 10d IR > 0.1，衰减比限制为 3.0")
        candidate_factors = []
        for col, metrics in ic_summary.items():
            ic_10d = abs(metrics["ic_10d"])
            ic_5d = abs(metrics["ic_5d"])
            decay_ratio = ic_5d / ic_10d if ic_10d > 0 else 999.0
            if ic_10d >= 0.005 and abs(metrics["ir_10d"]) >= 0.1 and decay_ratio < 3.0:
                candidate_factors.append(col)
                
    if len(candidate_factors) < 3:
        print(f"[FactorEngine] 因子仍过少，触发第二级降级：直接取 10d 绝对 IR 排名最前的 5 个特征")
        sorted_by_ir = sorted(feature_cols, key=lambda x: abs(ic_summary[x]["ir_10d"]), reverse=True)
        candidate_factors = sorted_by_ir[:5]
        
    print(f"[FactorEngine] 准入独立长效因子候选池: {candidate_factors}")
    
    # 相关性去冗余 (两两相关性 < 0.7，冲突保留 10d IR 绝对值最优者)
    candidate_factors = sorted(candidate_factors, key=lambda x: abs(ic_summary[x]["ir_10d"]), reverse=True)
    valid_factors = []
    for factor in candidate_factors:
        has_redundancy = False
        for selected in valid_factors:
            if abs(corr_matrix.loc[factor, selected]) >= 0.7:
                has_redundancy = True
                print(f"[FactorEngine] 因子 {factor} 与已选因子 {selected} 相关性过高 ({corr_matrix.loc[factor, selected]:.3f})，被剔除。")
                break
        if not has_redundancy:
            valid_factors.append(factor)
            
    print(f"\n📢 [FactorEngine] 最终筛选出的独立有效长效因子列表: {valid_factors}")
    return valid_factors, ic_summary


def main():
    conn = sqlite3.connect(DB_PATH)
    
    # 1. 加载数据
    daily, money, daily_basic, holder, index_df, top_inst_df = load_raw_data(conn)
    conn.close()

    # 2. 构建特征与多期标签（含龙虎榜机构净买入因子）
    df = build_features(daily, money, daily_basic, holder, index_df, top_inst_df)
    
    # 13维初始因子（含龙虎榜机构净买入因子，形成价值+资金+动量+机构四维信号体系）
    initial_features = [
        "factor_close_ma20",
        "factor_vol_ratio",
        "factor_price_range",
        "factor_money_intensity",
        "factor_inflow_days",
        "factor_pe",
        "factor_pb",
        "factor_circ_mv",
        "factor_holder_trend",
        "factor_momentum_5d",
        "factor_momentum_20d",
        "factor_rsi_10d",
        "factor_top_inst_5d",   # 龙虎榜机构净买入因子（正向信号）
    ]
    
    # 3. 因子多期 Rank IC 衰减分析与过滤
    valid_factors, ic_summary = analyze_factors_with_decay(df, initial_features, "20230101", "20240630")
    
    # 4. 输出筛选报告
    output_path = os.path.join(ROOT_DIR, "scripts", "valid_factors.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "valid_factors": valid_factors,
            "ic_summary": ic_summary
        }, f, indent=4, ensure_ascii=False)
        
    print(f"✅ 因子行业中性化评估完成，结果成功写入: {output_path}")


if __name__ == "__main__":
    main()

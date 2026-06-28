# -*- coding: utf-8 -*-
"""
train_ml_model.py —— 多因子主力吸筹机器学习建模与预测脚本 (重构镜像版)
=====================================================================
1. 特征工程：计算 9 维因子，利用 pd.merge_asof 对齐股东户数。
2. 镜像对齐：调用每日横截面行业中性化 (Ridge 哑变量残差) -> MAD 去极值 -> Z-Score 标准化。
3. 因子读取：自动载入由 factor_research_engine.py 筛选的 valid_factors。
4. 严格分割：训练集 (20230101-20240630), 验证集 (20240701-20241231), 测试集 (20250101-20251231)。
5. 结果持久化：将测试期预测得分写入 SQLite `ml_signals` 表。
"""

import os
import sys
import json
import sqlite3
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
import lightgbm as lgb
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, StackingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import LabelEncoder
import xgboost as xgb
import optuna
from sklearn.model_selection import cross_val_score

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")
sys.path.insert(0, ROOT_DIR)

def optuna_tune_lgb(X_train, y_train, trials=10):
    """
    使用 Optuna 对 LightGBM 基分类器进行快速超参寻优
    优化目标为 3 折交叉验证下的 precision (精确率)，以防漏报并严惩假阳性
    """
    print(f"[ML-OPTUNA] 启动 LightGBM 精准率超参调优，迭代次数: {trials}...")
    
    # 类别权重：0 (对应原始-1) -> 3.0, 1 (对应原始0) -> 1.0, 2 (对应原始1) -> 1.5
    weights_map = {0: 3.0, 1: 1.0, 2: 1.5}
    sample_weight = np.vectorize(weights_map.get)(y_train)

    def objective(trial):
        params = {
            'n_estimators': trial.suggest_int('n_estimators', 100, 300),
            'max_depth': trial.suggest_int('max_depth', 4, 8),
            'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.1, log=True),
            'num_leaves': trial.suggest_int('num_leaves', 15, 63),
            'subsample': trial.suggest_float('subsample', 0.6, 0.9),
            'colsample_bytree': trial.suggest_float('colsample_bytree', 0.6, 0.9),
            'random_state': 42,
            'verbosity': -1
        }
        # 使用均衡权重：大跌惩罚适中，避免模型过度保守从不预测正类
        model = lgb.LGBMClassifier(**params, class_weight={0: 2.0, 1: 1.0, 2: 1.5})
        
        # 3折交叉验证计算 ROC-AUC（三分类使用 ovr 策略），比 precision 更稳定
        # 注意：cross_val_score 不支持 fit_params 传 sample_weight（sklearn ≥ 1.4 已弃用此路径）
        # 改为手动循环 3 折
        try:
            from sklearn.model_selection import StratifiedKFold
            from sklearn.metrics import roc_auc_score
            kf = StratifiedKFold(n_splits=3, shuffle=True, random_state=42)
            auc_scores = []
            for tr_idx, val_idx in kf.split(X_train, y_train):
                X_tr, X_val = X_train[tr_idx], X_train[val_idx]
                y_tr, y_val = y_train[tr_idx], y_train[val_idx]
                sw_tr = sample_weight[tr_idx]
                model.fit(X_tr, y_tr, sample_weight=sw_tr)
                proba = model.predict_proba(X_val)
                auc = roc_auc_score(y_val, proba, multi_class='ovr', average='macro')
                auc_scores.append(auc)
            return float(np.mean(auc_scores))
        except Exception as e:
            return 0.0

    # 抑制 optuna 内部过多日志输出
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    
    study = optuna.create_study(direction='maximize')
    study.optimize(objective, n_trials=trials)
    
    print(f"[ML-OPTUNA] 调优完成！最优精确率: {study.best_value:.5f} | 最优参数: {study.best_params}")
    return study.best_params


class EnsembleModel:
    def __init__(self, best_lgb_params=None):
        lgb_params = {
            'n_estimators': 300,
            'max_depth': 6,
            'learning_rate': 0.03,
            'num_leaves': 31,
            'subsample': 0.7,
            'colsample_bytree': 0.7,
            'random_state': 42,
            'verbosity': -1,
            'class_weight': {0: 3.0, 1: 1.0, 2: 1.5}
        }
        if best_lgb_params:
            lgb_params.update(best_lgb_params)
            # 强行补充 class_weight 设定
            lgb_params['class_weight'] = {0: 3.0, 1: 1.0, 2: 1.5}
            
        # 移除慢速 GradientBoostingClassifier，只保留 LGB + RF + XGB
        # RF 规模缩小至 100 棵树，加快训练速度
        self.base_models = [
            ('lgb', lgb.LGBMClassifier(**lgb_params)),
            ('rf', RandomForestClassifier(
                n_estimators=100,
                max_depth=8,
                min_samples_split=50,
                n_jobs=-1,
                class_weight={0: 2.0, 1: 1.0, 2: 1.5},
                random_state=42
            )),
            ('xgb', xgb.XGBClassifier(
                n_estimators=150,
                max_depth=6,
                learning_rate=0.05,
                subsample=0.7,
                colsample_bytree=0.7,
                n_jobs=-1,
                random_state=42,
                eval_metric='mlogloss'
            ))
        ]
        
        self.clf = StackingClassifier(
            estimators=self.base_models,
            final_estimator=LogisticRegression(C=1.0, max_iter=1000, random_state=42),
            cv=3,
            n_jobs=-1
        )
        self.le = LabelEncoder()
    
    def fit(self, X, y):
        # 适配 LabelEncoder
        self.le.fit([-1, 0, 1])
        y_encoded = self.le.transform(y)
        
        # 类别权重：0(即大跌-1)->2.0, 1(中性0)->1.0, 2(上涨1)->1.5
        # 适度惩罚大跌股，但不要过度保守导致模型从不预测买入类
        weights_map = {0: 2.0, 1: 1.0, 2: 1.5}
        sample_weight = np.vectorize(weights_map.get)(y_encoded)
        
        print("[ML] 正在使用三折 Stacking 集成拟合三分类决策森林...")
        self.clf.fit(X, y_encoded, sample_weight=sample_weight)
    
    def predict_proba(self, X):
        class_1_idx = list(self.le.classes_).index(1)
        return self.clf.predict_proba(X)[:, class_1_idx]
    
    def predict(self, X):
        return (self.predict_proba(X) > 0.55).astype(int)



# =============================================================================
# 1. 核心数据加载
# =============================================================================
def load_raw_data(conn):
    print("[ML] 加载 2022-10-01 之后的数据（预留温热期，使用高效 SQL JOIN 过滤 ST 与无行业股票）...")
    
    daily = pd.read_sql("""
        SELECT dp.ts_code, dp.trade_date, dp.open, dp.high, dp.low, dp.close, dp.pct_chg, dp.vol, dp.amount, sl.name, sl.industry
        FROM daily_prices dp
        JOIN stock_list sl ON dp.ts_code = sl.ts_code
        WHERE dp.trade_date >= '20221001'
          AND sl.name NOT LIKE '%ST%'
          AND sl.industry IS NOT NULL
        ORDER BY dp.ts_code, dp.trade_date
    """, conn)

    money = pd.read_sql("""
        SELECT mf.ts_code, mf.trade_date, mf.buy_elg_amount, mf.sell_elg_amount, mf.buy_lg_amount, mf.sell_lg_amount
        FROM moneyflow mf
        JOIN stock_list sl ON mf.ts_code = sl.ts_code
        WHERE mf.trade_date >= '20221001'
          AND sl.name NOT LIKE '%ST%'
          AND sl.industry IS NOT NULL
    """, conn)
    for col in ["buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount"]:
        money[col] = pd.to_numeric(money[col]).fillna(0)
    money["net_main"] = (money["buy_elg_amount"] + money["buy_lg_amount"] -
                         money["sell_elg_amount"] - money["sell_lg_amount"])

    daily_basic = pd.read_sql("""
        SELECT db.ts_code, db.trade_date, db.pe, db.pb, db.circ_mv
        FROM daily_basic db
        JOIN stock_list sl ON db.ts_code = sl.ts_code
        WHERE db.trade_date >= '20221001'
          AND sl.name NOT LIKE '%ST%'
          AND sl.industry IS NOT NULL
    """, conn)

    holder = pd.read_sql("""
        SELECT h.ts_code, h.ann_date, h.end_date, h.holder_num
        FROM stk_holdernumber h
        JOIN stock_list sl ON h.ts_code = sl.ts_code
        WHERE (h.ann_date >= '20221001' OR h.end_date >= '20221001')
          AND sl.name NOT LIKE '%ST%'
          AND sl.industry IS NOT NULL
    """, conn)
    holder["holder_num"] = pd.to_numeric(holder["holder_num"]).fillna(0)
    holder["ann_date"] = holder["ann_date"].fillna(holder["end_date"])

    index_df = pd.read_sql("""
        SELECT trade_date, close as index_close
        FROM daily_index
        WHERE ts_code = '000001.SH' AND trade_date >= '20221001'
        ORDER BY trade_date
    """, conn)

    print("[ML] 加载筹码分布数据与北向资金数据...")
    chips = pd.read_sql("""
        WITH RankedChips AS (
            SELECT 
                c.ts_code, 
                c.trade_date, 
                c.price as peak_price,
                c.percent as chips_peak_pct,
                ROW_NUMBER() OVER (PARTITION BY c.ts_code, c.trade_date ORDER BY c.percent DESC, c.price ASC) as rn
            FROM cyq_chips c
            JOIN stock_list sl ON c.ts_code = sl.ts_code
            WHERE c.trade_date >= '20221001'
              AND sl.name NOT LIKE '%ST%'
              AND sl.industry IS NOT NULL
        ),
        WinnerChips AS (
            SELECT 
                c.ts_code, 
                c.trade_date,
                SUM(CASE WHEN c.price <= dp.close THEN c.percent ELSE 0 END) / 100.0 as winner_rate
            FROM cyq_chips c
            JOIN daily_prices dp ON c.ts_code = dp.ts_code AND c.trade_date = dp.trade_date
            JOIN stock_list sl ON c.ts_code = sl.ts_code
            WHERE c.trade_date >= '20221001'
              AND sl.name NOT LIKE '%ST%'
              AND sl.industry IS NOT NULL
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
    """, conn)

    hsgt = pd.read_sql("""
        SELECT trade_date, north_money
        FROM hsgt_moneyflow
        WHERE trade_date >= '20221001'
    """, conn)

    return daily, money, daily_basic, holder, index_df, chips, hsgt




# =============================================================================
# 2. 特征工程构建
# =============================================================================
def build_feature_engineering(daily, money, daily_basic, holder, index_df, chips, hsgt):
    print("[ML] 开始构建特征与未来 10 日标签...")
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

    # E. 标签构建: 未来 10 日超额收益率
    print("[ML] 正在计算未来 10 日超额收益作为预测标签...")
    df["future_close_10d"] = df.groupby("ts_code")["close"].shift(-10)
    df["stock_future_ret_10d"] = (df["future_close_10d"] - df["close"]) / df["close"]
    
    index_df = index_df.sort_values("trade_date").copy()
    index_df["index_future_close_10d"] = index_df["index_close"].shift(-10)
    index_df["index_future_ret_10d"] = (index_df["index_future_close_10d"] - index_df["index_close"]) / index_df["index_close"]
    
    df = df.merge(index_df[["trade_date", "index_future_ret_10d"]], on="trade_date", how="left")
    df["label_excess_ret"] = (df["stock_future_ret_10d"] - df["index_future_ret_10d"]).fillna(0.0)

    df = df[df["vol"] > 0].copy()
    return df


# =============================================================================
# 3. 镜像特征预处理：行业中性化 -> MAD 去极值 -> Z-Score 标准化
# =============================================================================
def preprocess_features_mirror(df, feature_cols):
    print("[ML] 镜像执行因子每日横截面行业中性化回归与标准化处理...")
    processed_df = df.copy()
    
    # 逐日横截面计算
    grouped = processed_df.groupby("trade_date")
    updated_dfs = []
    
    for date, day_data in grouped:
        if len(day_data) < 15:
            updated_dfs.append(day_data)
            continue
            
        day_data = day_data.copy()
        
        # 行业中性化
        industry_dummies = pd.get_dummies(day_data["industry"], drop_first=True, dtype=float)
        
        if not industry_dummies.empty:
            X = industry_dummies.values
            neutralize_model = Ridge(alpha=1.0)
            
            for col in feature_cols:
                y = day_data[col].values
                neutralize_model.fit(X, y)
                preds = neutralize_model.predict(X)
                day_data[col] = y - preds # 取残差
        
        # MAD + Z-Score
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
                
        updated_dfs.append(day_data)
        
    return pd.concat(updated_dfs).sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


# =============================================================================
# 4. 训练与预测主程序
# =============================================================================
def rolling_train_test_split(df, train_months=20, test_months=4):
    """滚动时间序列切分"""
    dates = sorted(df['trade_date'].unique())
    train_len = train_months * 20
    test_len = test_months * 20
    
    train_dates = dates[:train_len]
    test_dates = dates[train_len:train_len + test_len]
    
    print(f"[ML] 滚动划分: 训练期包含 {len(train_dates)} 个交易日 ({train_dates[0]}~{train_dates[-1]}) | 验证期包含 {len(test_dates)} 个交易日 ({test_dates[0]}~{test_dates[-1]})")
    return df[df['trade_date'].isin(train_dates)], df[df['trade_date'].isin(test_dates)]


def search_weights(models, X_val, y_val):
    """在验证集上进行网络搜索，以获取准确率最优的集成权重"""
    best_weight = {'lgb': 0.4, 'rf': 0.25, 'gbm': 0.25, 'lr': 0.1}
    best_acc = 0
    y_val_bin = (y_val > 0.0).astype(int)
    
    for w1 in [0.2, 0.3, 0.4, 0.5]:
        for w2 in [0.15, 0.2, 0.25, 0.3]:
            for w3 in [0.15, 0.2, 0.25, 0.3]:
                w4 = 1.0 - w1 - w2 - w3
                if w4 < 0.05 or w4 > 0.3:
                    continue
                weights = {'lgb': w1, 'rf': w2, 'gbm': w3, 'lr': w4}
                pred = 0
                for name, model in models.items():
                    proba = model.predict_proba(X_val)[:, 1]
                    pred += weights[name] * proba
                
                acc = (((pred > 0.55).astype(int)) == y_val_bin).mean()
                if acc > best_acc:
                    best_acc = acc
                    best_weight = weights
                    
    print(f"[ML] 最优集成权重搜索结果: {best_weight} | 验证集二分类准确率: {best_acc:.5f}")
    return best_weight



def main():
    conn = sqlite3.connect(DB_PATH)
    
    # 1. 核心数据加载
    daily, money, daily_basic, holder, index_df, chips, hsgt = load_raw_data(conn)
    
    # 2. 特征工程
    df = build_feature_engineering(daily, money, daily_basic, holder, index_df, chips, hsgt)
    
    # 3. 特征定义与有效因子加载
    all_features = [
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
        "factor_rsi_10d"
    ]
    
    new_features = [
        "washout_ready",
        "main_strength",
        "north_trend",
        "chip_delta",
        "momentum_accel",
        "factor_winner_rate",
        "factor_chip_peak_gap",
        "factor_sector_resonance"
    ]
    all_features.extend(new_features)
    
    valid_factors_path = os.path.join(ROOT_DIR, "scripts", "valid_factors.json")
    if os.path.exists(valid_factors_path):
        try:
            with open(valid_factors_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                feature_cols = config.get("valid_factors", all_features)
            # 确保新增特征必然包含在训练中
            for nf in new_features:
                if nf not in feature_cols:
                    feature_cols.append(nf)
            print(f"[ML] 成功加载行业中性化独立长效因子数量: {len(feature_cols)} -> {feature_cols}")
        except Exception as e:
            print(f"[ML] 读取 valid_factors.json 失败，使用全量因子Fallback: {e}")
            feature_cols = all_features
    else:
        print("[ML] 未发现 valid_factors.json，默认使用全量因子...")
        feature_cols = all_features
        
    # 4. 数据拆分 (滚动时间序列切分，防止未来函数)
    df_train_val = df[(df["trade_date"] >= "20230101") & (df["trade_date"] <= "20241231")].copy()
    df_train, df_val = rolling_train_test_split(df_train_val, train_months=20, test_months=4)
    # 测试期：2025-01-01 之后的所有数据
    df_test  = df[df["trade_date"] >= "20250101"].copy()
    
    print(f"[ML] 训练集样本数: {len(df_train):,} | 验证集样本数: {len(df_val):,} | 测试集样本数: {len(df_test):,}")
    
    # 5. 标准化特征
    df_train_scaled = preprocess_features_mirror(df_train, feature_cols)
    df_val_scaled   = preprocess_features_mirror(df_val, feature_cols)
    df_test_scaled  = preprocess_features_mirror(df_test, feature_cols)
    
    # 剔除含有缺失特征的样本
    df_train_scaled = df_train_scaled.dropna(subset=feature_cols + ["label_excess_ret"])
    df_val_scaled   = df_val_scaled.dropna(subset=feature_cols + ["label_excess_ret"])
    df_test_scaled  = df_test_scaled.dropna(subset=feature_cols)
    
    X_train = df_train_scaled[feature_cols].values
    y_train = df_train_scaled["label_excess_ret"].values
    
    X_val   = df_val_scaled[feature_cols].values
    y_val   = df_val_scaled["label_excess_ret"].values
    
    X_test  = df_test_scaled[feature_cols].values
    
    # 6. EnsembleModel 训练
    print("[ML] 训练中性化多因子 Ensemble 选股模型 (分类器概率集成)...")
    
    # 收益阈值三分类：THETA=1.5%（上涨），NEG_THRESHOLD=-1.5%（大跌）
    # 适当降低正类阈值以增加正类样本数量，改善类别不平衡
    THETA = 0.015
    NEG_THRESHOLD = -0.015
    y_train_bin = np.where(y_train >= THETA, 1, np.where(y_train < NEG_THRESHOLD, -1, 0))
    y_val_bin = np.where(y_val >= THETA, 1, np.where(y_val < NEG_THRESHOLD, -1, 0))
    
    # 打印类别分布
    unique, counts = np.unique(y_train_bin, return_counts=True)
    print(f'[ML] 训练集类别分布: {dict(zip(unique, counts))} (负类/-1，中性/0，正类/1)')
    
    # 对 LightGBM 进行 Optuna 调优，优化 precision_macro
    best_lgb_params = optuna_tune_lgb(X_train, y_train_bin, trials=15)
    
    model = EnsembleModel(best_lgb_params=best_lgb_params)
    model.fit(X_train, y_train_bin)
    
    # 7. 验证集评估
    val_preds = model.predict_proba(X_val)
    val_corr = pd.Series(val_preds).corr(pd.Series(y_val))
    print(f"[ML] 验证集评估 -> 中性化预测概率与真实超额收益相关系数: {val_corr:.5f}")
    
    # 8. 测试集样本外预测并落库
    print("[ML] 对测试集样本外进行预测概率计算...")
    preds = model.predict_proba(X_test)
    df_test_scaled["pred_score"] = preds
    
    # 衍生计算左右侧信号
    pct_change_20 = df_test_scaled.groupby('ts_code')['close'].transform(lambda x: x.pct_change(20))
    df_test_scaled['left_signal'] = ((pct_change_20 < -0.05) & (df_test_scaled['pred_score'] > 0.6)).astype(int)
    
    pct_change_5 = df_test_scaled.groupby('ts_code')['close'].transform(lambda x: x.pct_change(5))
    df_test_scaled['right_signal'] = ((pct_change_5 > 0.0) & (df_test_scaled['pred_score'] > 0.7)).astype(int)

    df_test_scaled['signal_type'] = df_test_scaled.apply(
        lambda row: 'left' if row['left_signal'] == 1 else 'right' if row['right_signal'] == 1 else 'none',
        axis=1
    )
    
    # 结果写入数据库 (防重复存储)
    print("[ML] 将测试集个股中性化预测得分落库至 `ml_signals` 表...")
    conn.execute("DROP TABLE IF EXISTS ml_signals")
    conn.execute("""
        CREATE TABLE ml_signals (
            trade_date TEXT NOT NULL,
            ts_code    TEXT NOT NULL,
            score      REAL,
            signal_type TEXT,
            left_signal INTEGER,
            right_signal INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (trade_date, ts_code)
        )
    """)
    conn.execute("CREATE INDEX idx_mls_date ON ml_signals (trade_date)")
    
    rows = [
        (str(r.trade_date), str(r.ts_code), float(r.pred_score), str(r.signal_type), int(r.left_signal), int(r.right_signal))
        for r in df_test_scaled[["trade_date", "ts_code", "pred_score", "signal_type", "left_signal", "right_signal"]].itertuples(index=False)
    ]
    
    conn.executemany("""
        INSERT OR REPLACE INTO ml_signals (trade_date, ts_code, score, signal_type, left_signal, right_signal)
        VALUES (?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    
    print(f"✅ 成功写入 {len(rows):,} 条中性化样本外预测信号至本地缓存！")
    
    # 保存模型和标准化参数供推理使用
    print("[ML] 保存模型和标准化参数到 models/ 目录...")
    import joblib
    
    models_dir = os.path.join(ROOT_DIR, "models")
    os.makedirs(models_dir, exist_ok=True)
    
    scalers = {}
    for f in feature_cols:
        scalers[f"mean_{f}"] = float(df_train_scaled[f].mean())
        std_val = float(df_train_scaled[f].std())
        scalers[f"std_{f}"] = std_val if std_val > 0 else 1.0
    
    industry_means = {}
    for f in feature_cols:
        means = df_train_scaled.groupby("industry")[f].mean()
        industry_means[f] = means.to_dict()
    
    # 保存模型文件
    joblib.dump(model, os.path.join(models_dir, "ensemble_model.pkl"))
    joblib.dump(scalers, os.path.join(models_dir, "factor_scalers.pkl"))
    joblib.dump(industry_means, os.path.join(models_dir, "industry_means.pkl"))
    
    print(f"✅ 模型文件已保存到 {models_dir}/")
    print(f"   - ensemble_model.pkl (Ensemble分类集成模型)")
    print(f"   - factor_scalers.pkl (因子标准化参数)")
    print(f"   - industry_means.pkl (行业均值)")
    
    conn.close()



if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
机器学习策略 - 使用分类模型预测涨跌概率
"""
import sys
import os
import json
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

def prepare_features(df):
    """准备特征数据"""
    features = df.copy()
    
    features['pct_chg_ma5'] = features.groupby('ts_code')['pct_chg'].rolling(5).mean().reset_index(0, drop=True)
    features['pct_chg_ma20'] = features.groupby('ts_code')['pct_chg'].rolling(20).mean().reset_index(0, drop=True)
    features['vol_ratio'] = features.groupby('ts_code')['vol'].rolling(5).mean().reset_index(0, drop=True) / (features.groupby('ts_code')['vol'].rolling(20).mean().reset_index(0, drop=True) + 1e-10)
    features['ret_5d'] = features.groupby('ts_code')['close'].pct_change(5).fillna(0)
    features['ret_10d'] = features.groupby('ts_code')['close'].pct_change(10).fillna(0)
    features['ret_20d'] = features.groupby('ts_code')['close'].pct_change(20).fillna(0)
    
    features['volatility'] = features.groupby('ts_code')['pct_chg'].rolling(20).std().reset_index(0, drop=True)
    
    features['ma5_above_ma20'] = (features.groupby('ts_code')['close'].rolling(5).mean().reset_index(0, drop=True) > 
                                   features.groupby('ts_code')['close'].rolling(20).mean().reset_index(0, drop=True)).astype(int)
    
    if 'winner_rate' in features.columns:
        features['winner_rate'] = features['winner_rate'].fillna(0.5)
    else:
        features['winner_rate'] = 0.5
    
    return features

def create_target(df, hold_days=10):
    """创建目标变量：未来N天是否上涨"""
    df = df.copy()
    df['future_return'] = df.groupby('ts_code')['close'].shift(-hold_days) / df['close'] - 1
    df['target'] = (df['future_return'] > 0.02).astype(int)
    
    return df.dropna(subset=['target'])

def train_model(X, y):
    """训练分类模型"""
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=8,
        min_samples_split=10,
        min_samples_leaf=5,
        random_state=42
    )
    
    model.fit(X_scaled, y)
    
    return model, scaler

def backtest_ml_strategy(executor, top_n=10, prob_threshold=0.55):
    """回测机器学习策略"""
    daily = executor.load_data()
    df = daily[daily['trade_date'] >= executor.start_date].copy()
    
    df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
    df = prepare_features(df)
    df = create_target(df)
    
    feature_cols = ['pb', 'circ_mv_yi', 'ret_5d', 'ret_10d', 'ret_20d', 
                   'volatility', 'ma5_above_ma20', 'winner_rate', 'pct_chg']
    
    df = df.dropna(subset=feature_cols + ['target'])
    
    train_df = df[df['trade_date'] < pd.to_datetime('2026-04-01')]
    test_df = df[df['trade_date'] >= pd.to_datetime('2026-04-01')]
    
    X_train = train_df[feature_cols].values
    y_train = train_df['target'].values
    
    X_test = test_df[feature_cols].values
    
    model, scaler = train_model(X_train, y_train)
    
    X_test_scaled = scaler.transform(X_test)
    test_df['prob'] = model.predict_proba(X_test_scaled)[:, 1]
    
    train_pred = model.predict(scaler.transform(X_train))
    train_acc = accuracy_score(y_train, train_pred)
    print(f"训练集准确率: {train_acc:.1%}")
    
    df['signal'] = False
    
    test_df = test_df.sort_values('trade_date')
    
    for date in test_df['trade_date'].unique():
        day_df = test_df[test_df['trade_date'] == date]
        day_df = day_df[day_df['prob'] >= prob_threshold]
        
        if len(day_df) >= top_n:
            selected = day_df.sort_values('prob', ascending=False).head(top_n)
            df.loc[selected.index, 'signal'] = True
        elif len(day_df) > 0:
            df.loc[day_df.index, 'signal'] = True
    
    signal_df = df[df['signal']]
    signal_count = len(signal_df)
    total_days = len(test_df['trade_date'].unique())
    signal_per_day = signal_count / total_days if total_days > 0 else 0
    
    trades = []
    positions = {}
    
    for date in sorted(test_df['trade_date'].unique()):
        day_df = df[df['trade_date'] == date]
        signals = day_df[day_df['signal']]['ts_code'].tolist()
        
        closed = []
        for code, pos in list(positions.items()):
            pos['days'] += 1
            if pos['days'] >= 10:
                code_df = day_df[day_df['ts_code'] == code]
                exit_price = float(code_df['close'].iloc[0]) if not code_df.empty else pos['entry_price']
                trades.append({
                    'entry_date': pos['entry_date'],
                    'exit_date': date,
                    'entry_price': pos['entry_price'],
                    'exit_price': exit_price,
                    'return': (exit_price - pos['entry_price']) / pos['entry_price']
                })
                closed.append(code)
        
        for code in closed:
            del positions[code]
        
        for code in signals:
            if code not in positions:
                entry_price = float(day_df[day_df['ts_code'] == code]['close'].iloc[0])
                positions[code] = {
                    'entry_date': date,
                    'entry_price': entry_price,
                    'days': 0
                }
    
    if trades:
        trade_df = pd.DataFrame(trades)
        win_rate = (trade_df['return'] > 0).mean()
        total_return = (1 + trade_df['return']).prod() - 1
        equity = (1 + trade_df['return']).cumprod()
        max_drawdown = (1 - equity / equity.cummax()).max()
        trade_count = len(trades)
    else:
        win_rate = 0.0
        total_return = 0.0
        max_drawdown = 0.0
        trade_count = 0
    
    return {
        'win_rate': win_rate,
        'total_return': total_return,
        'max_drawdown': max_drawdown,
        'signal_per_day': signal_per_day,
        'trade_count': trade_count,
        'train_accuracy': train_acc,
        'top_n': top_n,
        'prob_threshold': prob_threshold
    }

def main():
    executor = BacktestExecutor("20260101", "20260625")
    
    print("="*70)
    print("          机器学习策略测试")
    print("="*70)
    
    results = []
    
    for top_n in [5, 8, 10]:
        for prob_threshold in [0.52, 0.55, 0.58, 0.60]:
            print(f"\n--- Top-{top_n}, 概率阈值: {prob_threshold} ---")
            
            result = backtest_ml_strategy(executor, top_n, prob_threshold)
            
            print(f"训练准确率: {result['train_accuracy']:.1%}")
            print(f"胜率: {result['win_rate']:.1%}")
            print(f"总收益: {result['total_return']:.1%}")
            print(f"最大回撤: {result['max_drawdown']:.1%}")
            print(f"信号数: {result['signal_per_day']:.1f}/天")
            print(f"交易数: {result['trade_count']}")
            
            meets_constraints = (
                result['win_rate'] >= 0.55 and result['win_rate'] <= 0.65 and
                result['signal_per_day'] >= 5 and result['signal_per_day'] <= 10 and
                result['max_drawdown'] <= 0.20 and
                result['trade_count'] >= 10
            )
            
            if meets_constraints:
                print("✅ 满足所有约束！")
            
            results.append(result)
    
    print(f"\n\n{'='*70}")
    print("          结果汇总")
    print("="*70)
    
    for r in results:
        status = "✅" if (
            r['win_rate'] >= 0.55 and r['win_rate'] <= 0.65 and
            r['signal_per_day'] >= 5 and r['signal_per_day'] <= 10 and
            r['max_drawdown'] <= 0.20 and
            r['trade_count'] >= 10
        ) else "❌"
        
        print(f"Top-{r['top_n']}, 阈值{r['prob_threshold']}: 胜率={r['win_rate']:.1%}, 信号={r['signal_per_day']:.1f}/天, 回撤={r['max_drawdown']:.1%}, 收益={r['total_return']:.1%} {status}")

if __name__ == "__main__":
    main()
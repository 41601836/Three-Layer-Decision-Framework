# -*- coding: utf-8 -*-
"""检查因子与未来收益的相关性"""
import sys
import os
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

executor = BacktestExecutor('20260101', '20260625')
daily = executor.load_data()

df = daily[daily['trade_date'] >= executor.start_date].copy()
df = df.dropna(subset=['close', 'pb', 'ret_20d'])

df['future_return'] = df.groupby('ts_code')['close'].shift(-10) / df['close'] - 1

print('因子与未来收益的相关性:')
corr = df[['pb', 'ret_20d', 'circ_mv', 'pct_chg', 'roe', 'volatility_20d', 'future_return']].corr()['future_return']
print(corr)

print('\n按因子分位分组的平均未来收益:')
for factor in ['pb', 'ret_20d']:
    df['quantile'] = pd.qcut(df[factor], 5, labels=False)
    avg_return = df.groupby('quantile')['future_return'].mean()
    print(f'{factor}:')
    print(avg_return)
    print()

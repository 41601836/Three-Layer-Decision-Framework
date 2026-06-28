# -*- coding: utf-8 -*-
"""检查circ_mv数据范围"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

executor = BacktestExecutor('20260101', '20260625')
daily = executor.load_data()

df = daily[daily['trade_date'] >= executor.start_date].copy()
df = df.dropna(subset=['circ_mv'])

print('circ_mv统计:')
print(df['circ_mv'].describe())
print('\ncirc_mv最小值:', df['circ_mv'].min())
print('circ_mv最大值:', df['circ_mv'].max())
print('circ_mv中位数:', df['circ_mv'].median())
print('circ_mv大于1000亿的数量:', len(df[df['circ_mv'] > 1000]))
print('circ_mv在10-500亿之间的数量:', len(df[(df['circ_mv'] >= 10) & (df['circ_mv'] <= 500)]))
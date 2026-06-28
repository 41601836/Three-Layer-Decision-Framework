# -*- coding: utf-8 -*-
"""检查数据情况"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

executor = BacktestExecutor('20260101', '20260625')
daily = executor.load_data()

print('数据时间范围:', daily['trade_date'].min(), '到', daily['trade_date'].max())
print('数据列:', daily.columns.tolist())
print('样本数:', len(daily))
print('\n数据前5行:')
print(daily.head())
print('\nwinner_rate统计:')
print(daily['winner_rate'].describe())
# -*- coding: utf-8 -*-
"""分析回测信号质量 — 修正列名 + 去重 + 限定日期"""
import sqlite3, pandas as pd

conn = sqlite3.connect("db/stock_daily.db")

# 1. 读取本次回测信号（2024-01-02 ~ 2024-06-28）
signals = pd.read_sql("""
    SELECT * FROM backtest_signals 
    WHERE signal_date BETWEEN '20240102' AND '20240628'
""", conn)
print(f"📊 本次回测信号数: {len(signals)}")

# 2. 读取未来收益
price_data = pd.read_sql("""
    SELECT ts_code, trade_date, close,
           LEAD(close, 5) OVER (PARTITION BY ts_code ORDER BY trade_date) as close_5d,
           LEAD(close, 10) OVER (PARTITION BY ts_code ORDER BY trade_date) as close_10d,
           LEAD(close, 20) OVER (PARTITION BY ts_code ORDER BY trade_date) as close_20d
    FROM daily_prices
""", conn)

# 3. 关联（用 signal_date = trade_date）
merged = signals.merge(price_data, left_on=['ts_code','signal_date'], right_on=['ts_code','trade_date'], how='left')
merged['ret_5d'] = merged['close_5d'] / merged['close'] - 1
merged['ret_10d'] = merged['close_10d'] / merged['close'] - 1
merged['ret_20d'] = merged['close_20d'] / merged['close'] - 1

# 4. 按得分分组
print("\n📈 按得分分组 (≥10, ≥20):")
for score_min in [10, 15, 20, 25, 30]:
    sub = merged[merged['score'] >= score_min]
    if len(sub) == 0: continue
    win_5 = (sub['ret_5d'] > 0).mean()
    win_10 = (sub['ret_10d'] > 0).mean()
    avg_10 = sub['ret_10d'].mean()
    print(f"得分≥{score_min}: 样本{len(sub)} 胜率(5日){win_5:.1%} 胜率(10日){win_10:.1%} 平均10日收益{avg_10:.2%}")

# 5. 按因子组合
print("\n📊 按因子组合 (振幅/筹码/资金):")
merged['combo'] = merged.apply(
    lambda r: f"{int(r['volume_price']>0)}{int(r['chip_structure']>0)}{int(r['market_behavior']>0)}", axis=1
)
for combo, grp in merged.groupby('combo'):
    if len(grp) < 50: continue
    win_10 = (grp['ret_10d'] > 0).mean()
    avg_10 = grp['ret_10d'].mean()
    print(f"组合 {combo}: 样本{len(grp)} 胜率(10日){win_10:.1%} 平均收益{avg_10:.2%}")

conn.close()
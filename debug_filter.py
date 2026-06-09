# -*- coding: utf-8 -*-
"""调试filter_engine的熔断问题"""
import sqlite3
import sys
sys.path.insert(0, '.')

conn = sqlite3.connect('db/stock_daily.db')

# 获取一些股票代码来测试
cur = conn.cursor()
cur.execute("SELECT ts_code FROM daily_prices WHERE trade_date='20260608' LIMIT 20")
codes = [row[0] for row in cur.fetchall()]
conn.close()

print(f"测试股票代码: {codes}")

# 尝试逐个评分
from filter_engine import ScoreCardBuilder

builder = ScoreCardBuilder()

for code in codes:
    try:
        result = builder.build(code, conn=None)
        print(f"✓ {code}: 成功")
    except Exception as e:
        print(f"✗ {code}: {e}")
        import traceback
        traceback.print_exc()
        break
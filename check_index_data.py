# -*- coding: utf-8 -*-
import sqlite3

conn = sqlite3.connect('db/stock_daily.db')
cur = conn.cursor()

# 获取所有表
cur.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = cur.fetchall()
print('All tables:', [t[0] for t in tables])

# 检查daily_prices中是否有指数
cur.execute('SELECT DISTINCT ts_code FROM daily_prices WHERE ts_code LIKE "%SH%" LIMIT 10')
index_codes = cur.fetchall()
print('Index codes in daily_prices:', index_codes)

# 检查000001.SH
cur.execute('SELECT COUNT(*) FROM daily_prices WHERE ts_code = "000001.SH"')
count = cur.fetchone()[0]
print('000001.SH rows in daily_prices:', count)

# 检查日期范围
cur.execute('SELECT MIN(trade_date), MAX(trade_date) FROM daily_prices WHERE ts_code = "000001.SH"')
date_range = cur.fetchone()
print('000001.SH date range:', date_range)

# 检查是否有daily_index表
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_index'")
daily_index = cur.fetchall()
print('daily_index table exists:', daily_index)

conn.close()

import sqlite3
conn = sqlite3.connect('db/stock_daily.db')
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM daily_index WHERE ts_code = '000001.SH'")
count = cur.fetchone()[0]
cur.execute("SELECT MIN(trade_date), MAX(trade_date) FROM daily_index WHERE ts_code = '000001.SH'")
date_range = cur.fetchone()
print(f"daily_index表中000001.SH数据: {count}条")
print(f"日期范围: {date_range[0]} ~ {date_range[1]}")
conn.close()
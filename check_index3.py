import sqlite3
conn = sqlite3.connect('db/stock_daily.db')
cnt = conn.execute("SELECT COUNT(*) FROM daily_prices WHERE ts_code='000001.SH'").fetchone()[0]
print(f"上证指数数据条数: {cnt}")
conn.close()
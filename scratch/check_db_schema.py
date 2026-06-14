import sqlite3
import os

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db", "stock_daily.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 找一个最近有数据的股票
cursor.execute("SELECT MAX(trade_date) FROM daily_prices")
latest_date = cursor.fetchone()[0]
print(f"Latest Date: {latest_date}")

# 查这个日期的几只股票
cursor.execute("""
    SELECT dp.ts_code, dp.trade_date, dp.close, dp.vol, dp.amount, db.turnover_rate, db.volume_ratio
    FROM daily_prices dp
    LEFT JOIN daily_basic db ON dp.ts_code = db.ts_code AND dp.trade_date = db.trade_date
    WHERE dp.trade_date = ?
    LIMIT 5
""", (latest_date,))
rows = cursor.fetchall()
for row in rows:
    print(row)

conn.close()

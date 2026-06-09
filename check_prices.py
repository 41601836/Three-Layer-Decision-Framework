
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "db", "stock_daily.db")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

print("=== 检查几只股票的最新收盘价 ===")
target_stocks = ["600981.SH", "603605.SH", "600467.SH"]

for ts_code in target_stocks:
    print(f"\n--- {ts_code} ---")
    cursor.execute("""
        SELECT trade_date, close, pct_chg 
        FROM daily_prices 
        WHERE ts_code = ? 
        ORDER BY trade_date DESC LIMIT 5
    """, (ts_code,))
    rows = cursor.fetchall()
    for row in rows:
        print(f"  {row[0]}: ¥{row[1]:.2f}, 涨跌幅: {row[2]:.2f}%")

conn.close()

print("\n=== 时间问题 ===")
import datetime
print(f"当前系统时间: {datetime.datetime.now()}")

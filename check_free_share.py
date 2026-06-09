import sqlite3
conn = sqlite3.connect("d:/StockAI/db/stock_daily.db")

print("=== stock_list 表结构 ===")
cursor = conn.execute("PRAGMA table_info(stock_list)")
for row in cursor.fetchall():
    print(f"{row[0]}: {row[1]} ({row[2]})")

print("\n=== stock_list 中福蓉科技数据 ===")
cursor = conn.execute("SELECT * FROM stock_list WHERE ts_code='603327.SH'")
row = cursor.fetchone()
print(row)

print("\n=== daily_basic 最新数据 ===")
cursor = conn.execute("""
    SELECT trade_date, circ_mv, float_share 
    FROM daily_basic 
    WHERE ts_code='603327.SH' 
    ORDER BY trade_date DESC 
    LIMIT 1
""")
row = cursor.fetchone()
print(f"日期: {row[0]}")
print(f"circ_mv(流通市值,万元): {row[1]}")
print(f"float_share(流通股本,万股): {row[2]}")

conn.close()
import sqlite3
conn = sqlite3.connect("d:/StockAI/db/stock_daily.db")

print("=== daily_basic 表结构 ===")
cursor = conn.execute("PRAGMA table_info(daily_basic)")
for row in cursor.fetchall():
    print(f"{row[0]}: {row[1]} ({row[2]})")

print("\n=== daily_prices 表结构 ===")
cursor = conn.execute("PRAGMA table_info(daily_prices)")
for row in cursor.fetchall():
    print(f"{row[0]}: {row[1]} ({row[2]})")

print("\n=== 福蓉科技6月初每日成交量(daily_prices) ===")
cursor = conn.execute("""
    SELECT trade_date, vol 
    FROM daily_prices 
    WHERE ts_code='603327.SH' AND trade_date BETWEEN '20260601' AND '20260608'
    ORDER BY trade_date
""")
print(f"{'日期':<12} {'成交量(手)':<15}")
print("-" * 27)
vol_sum = 0
count = 0
for row in cursor.fetchall():
    print(f"{row[0]:<12} {row[1]:<15}")
    vol_sum += row[1]
    count += 1

avg_vol = vol_sum / count
print(f"\n平均日成交量: {avg_vol:.0f} 手")

print("\n=== daily_basic 换手率数据 ===")
cursor = conn.execute("""
    SELECT trade_date, turnover_rate, float_share 
    FROM daily_basic 
    WHERE ts_code='603327.SH' AND trade_date BETWEEN '20260601' AND '20260608'
    ORDER BY trade_date
""")
print(f"{'日期':<12} {'换手率(%)':<12} {'流通股本(万股)':<15}")
print("-" * 42)
for row in cursor.fetchall():
    print(f"{row[0]:<12} {row[1]:<12.2f} {row[2]:<15}")

conn.close()
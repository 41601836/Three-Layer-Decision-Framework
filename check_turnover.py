import sqlite3
conn = sqlite3.connect("db/stock_daily.db")

print("=== daily_basic 表结构 ===")
cursor = conn.execute("PRAGMA table_info(daily_basic)")
print(f"{'序号':<4} {'字段名':<20} {'类型':<15} {'是否可为空':<8}")
print("-" * 50)
for row in cursor.fetchall():
    print(f"{row[0]:<4} {row[1]:<20} {row[2]:<15} {row[3]:<8}")

print("\n=== 福蓉科技(603327.SH) 6月初 daily_basic 原始数据 ===")
print(f"{'日期':<12} {'换手率(%)':<12} {'量比':<8}")
print("-" * 35)
for row in conn.execute("""
    SELECT trade_date, turnover_rate, volume_ratio 
    FROM daily_basic 
    WHERE ts_code = '603327.SH' AND trade_date BETWEEN '20260601' AND '20260608' 
    ORDER BY trade_date
"""):
    print(f"{row[0]:<12} {row[1]:<12.2f} {row[2]:<8.2f}")

print("\n=== 最近20日平均换手率 ===")
avg_row = conn.execute("""
    SELECT AVG(turnover_rate)
    FROM daily_basic 
    WHERE ts_code = '603327.SH' AND trade_date >= '20260520'
""").fetchone()
print(f"平均换手率: {avg_row[0]:.2f}%")

conn.close()
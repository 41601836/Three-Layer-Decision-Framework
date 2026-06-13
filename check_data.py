import sqlite3
conn = sqlite3.connect("db/stock_daily.db")

print("=== 检查数据库表数据 ===")
print("\n1. 600519.SH daily_basic 表（近3条）")
for row in conn.execute("SELECT trade_date, circ_mv FROM daily_basic WHERE ts_code='600519.SH' ORDER BY trade_date DESC LIMIT 3"):
    print(row)

print("\n2. 600519.SH daily_prices 最新收盘价")
row = conn.execute("SELECT trade_date, close FROM daily_prices WHERE ts_code='600519.SH' ORDER BY trade_date DESC LIMIT 1").fetchone()
print(row)

print("\n3. 600519.SH bak_basic 表（流通股本）")
for row in conn.execute("SELECT trade_date, float_share FROM bak_basic WHERE ts_code='600519.SH' ORDER BY trade_date DESC LIMIT 1"):
    print(row)

print("\n4. industry_rank 表（前5条）")
for row in conn.execute("SELECT * FROM industry_rank LIMIT 5"):
    print(row)

print("\n5. stock_list 表 600519.SH 的行业")
row = conn.execute("SELECT industry FROM stock_list WHERE ts_code='600519.SH'").fetchone()
print(row)

conn.close()

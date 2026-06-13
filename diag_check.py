import sqlite3
conn = sqlite3.connect('db/stock_daily.db')

# 数据覆盖范围
r = conn.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT trade_date) FROM daily_prices").fetchone()
print(f"daily_prices: {r[0]} ~ {r[1]} ({r[2]} 个交易日)")

r2 = conn.execute("SELECT MIN(trade_date), MAX(trade_date) FROM moneyflow").fetchone()
print(f"moneyflow:    {r2[0]} ~ {r2[1]}")

r3 = conn.execute("SELECT MIN(trade_date), MAX(trade_date) FROM daily_basic").fetchone()
print(f"daily_basic:  {r3[0]} ~ {r3[1]}")

r4 = conn.execute("SELECT MIN(trade_date), MAX(trade_date) FROM margin_detail").fetchone()
print(f"margin_detail:{r4[0]} ~ {r4[1]}")

# 检查 2024年 数据覆盖
cnt_2024 = conn.execute("SELECT COUNT(DISTINCT trade_date) FROM daily_prices WHERE trade_date BETWEEN '20240101' AND '20241231'").fetchone()[0]
cnt_2024_mf = conn.execute("SELECT COUNT(DISTINCT trade_date) FROM moneyflow WHERE trade_date BETWEEN '20240101' AND '20241231'").fetchone()[0]
print(f"\n2024年覆盖: daily_prices={cnt_2024}日, moneyflow={cnt_2024_mf}日")

# 202406 样本
cnt_jun = conn.execute("SELECT COUNT(DISTINCT trade_date) FROM daily_prices WHERE trade_date BETWEEN '20240601' AND '20240630'").fetchone()[0]
dates_jun = conn.execute("SELECT DISTINCT trade_date FROM daily_prices WHERE trade_date BETWEEN '20240601' AND '20240630' ORDER BY trade_date").fetchall()
print(f"\n2024-06 交易日: {cnt_jun} 天")
print("日期:", [r[0] for r in dates_jun])

# 检查是否有 open 字段（买入价用次日开盘）
cols = conn.execute("PRAGMA table_info(daily_prices)").fetchall()
print(f"\ndaily_prices字段: {[c[1] for c in cols]}")

conn.close()

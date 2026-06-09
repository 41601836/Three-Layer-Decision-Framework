import sqlite3
db = sqlite3.connect('db/stock_daily.db')
r2023 = db.execute("SELECT COUNT(*) FROM hsgt_moneyflow WHERE trade_date LIKE '2023%'").fetchone()[0]
r2024 = db.execute("SELECT COUNT(*) FROM hsgt_moneyflow WHERE trade_date LIKE '2024%'").fetchone()[0]
r2025 = db.execute("SELECT COUNT(*) FROM hsgt_moneyflow WHERE trade_date LIKE '2025%'").fetchone()[0]
rmin  = db.execute("SELECT MIN(trade_date) FROM hsgt_moneyflow").fetchone()[0]
rmax  = db.execute("SELECT MAX(trade_date) FROM hsgt_moneyflow").fetchone()[0]
print(f"2023: {r2023} | 2024: {r2024} | 2025: {r2025}")
print(f"Range: {rmin} ~ {rmax}")
# circ_mv coverage
try:
    cv = db.execute("SELECT COUNT(DISTINCT ts_code) FROM daily_basic WHERE circ_mv IS NOT NULL AND trade_date >= '20250101'").fetchone()[0]
    print(f"daily_basic circ_mv 覆盖股票数(2025): {cv}")
except Exception as e:
    print(f"daily_basic: {e}")
db.close()

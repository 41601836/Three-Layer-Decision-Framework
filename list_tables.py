import sqlite3
conn = sqlite3.connect('db/stock_daily.db')
# margin_detail 列名
print("=== margin_detail ===")
cols = conn.execute("PRAGMA table_info(margin_detail)").fetchall()
for c in cols:
    print(f"  {c[1]} ({c[2]})")
# hsgt_moneyflow 列名
print("=== hsgt_moneyflow ===")
cols = conn.execute("PRAGMA table_info(hsgt_moneyflow)").fetchall()
for c in cols:
    print(f"  {c[1]} ({c[2]})")
# 看看 hsgt_moneyflow 有无2025年数据
print("=== hsgt_moneyflow 2025 count ===")
r = conn.execute("SELECT COUNT(*) FROM hsgt_moneyflow WHERE trade_date >= '20250101'").fetchone()
print(f"  2025年行数: {r[0]}")
# margin_detail 样本
print("=== margin_detail sample ===")
cols2 = [c[1] for c in conn.execute("PRAGMA table_info(margin_detail)").fetchall()]
print("  columns:", cols2)
conn.close()

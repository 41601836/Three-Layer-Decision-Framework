import sqlite3
conn = sqlite3.connect("d:/StockAI/db/stock_daily.db")

print("=== 检查白酒行业数据 ===")
rows = conn.execute("SELECT * FROM industry_rank WHERE industry LIKE '%白酒%'").fetchall()
if rows:
    for row in rows:
        print(row)
else:
    print("白酒行业在 industry_rank 表中没有数据！")
    print("\n所有行业列表（前20条）:")
    for row in conn.execute("SELECT DISTINCT industry FROM industry_rank LIMIT 20"):
        print(row[0])

conn.close()

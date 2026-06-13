import sqlite3
conn = sqlite3.connect("db/stock_daily.db")

print("=== 最新日期白酒行业数据 ===")
for row in conn.execute("SELECT * FROM industry_rank WHERE industry='白酒' ORDER BY calc_date DESC LIMIT 2"):
    print(row)

print("\n=== 最新日期所有行业排名（按综合得分降序） ===")
rank = 1
for row in conn.execute("""
    SELECT industry, composite_score, tier 
    FROM industry_rank 
    WHERE calc_date = (SELECT MAX(calc_date) FROM industry_rank)
    ORDER BY composite_score DESC
"""):
    print(f"{rank:2d}. {row[0]} 得分:{row[1]:.3f} 等级:{row[2]}")
    rank += 1
    if rank > 20:
        break

conn.close()

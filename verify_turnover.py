import sqlite3
import pandas as pd
conn = sqlite3.connect("d:/StockAI/db/stock_daily.db")

print("=== 验证换手率计算 ===")

# 获取最近20天的成交量
cursor = conn.execute("""
    SELECT trade_date, vol 
    FROM daily_prices 
    WHERE ts_code='603327.SH' 
    ORDER BY trade_date DESC 
    LIMIT 20
""")
vol_data = cursor.fetchall()
vol_sum = sum(row[1] for row in vol_data)
avg_vol = vol_sum / len(vol_data)
print(f"最近20天平均成交量: {avg_vol:.0f} 手")

# 获取流通股本
cursor = conn.execute("SELECT float_share FROM daily_basic WHERE ts_code='603327.SH' ORDER BY trade_date DESC LIMIT 1")
float_share = cursor.fetchone()[0]
circ_cap = float_share / 10000  # 万股转亿股
print(f"流通股本: {float_share} 万股 = {circ_cap:.4f} 亿股")

# 计算换手率
turnover = avg_vol / (circ_cap * 10000)
print(f"计算换手率: {avg_vol:.0f} / ({circ_cap:.4f} * 10000) = {turnover:.4f}%")

# 对比数据库中的换手率
print("\n=== 数据库原始换手率 ===")
cursor = conn.execute("""
    SELECT trade_date, turnover_rate 
    FROM daily_basic 
    WHERE ts_code='603327.SH' 
    ORDER BY trade_date DESC 
    LIMIT 20
""")
rates = []
for row in cursor.fetchall():
    print(f"{row[0]}: {row[1]:.2f}%")
    rates.append(row[1])

print(f"\n最近20天平均换手率: {sum(rates)/len(rates):.2f}%")

conn.close()
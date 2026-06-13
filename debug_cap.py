import sqlite3

DB_PATH = "db/stock_daily.db"

def get_circulating_cap(ts_code):
    """从daily_basic获取最新流通股本（亿股）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT float_share FROM daily_basic WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1", (ts_code,))
        row = cursor.fetchone()
        conn.close()
        print(f"查询结果: {row}")
        if row and row[0] and row[0] > 0:
            result = row[0] / 10000
            print(f"流通股本: {row[0]} 万股 = {result} 亿股")
            return result
        print("使用默认值: 5亿股")
        return 5
    except Exception as e:
        print(f"异常: {e}")
        return 5

print("=== 测试 get_circulating_cap ===")
cap = get_circulating_cap('603327.SH')
print(f"最终返回: {cap} 亿股")

# 验证数据库查询
print("\n=== 直接验证数据库 ===")
conn = sqlite3.connect(DB_PATH)
cursor = conn.execute("SELECT float_share, trade_date FROM daily_basic WHERE ts_code='603327.SH' ORDER BY trade_date DESC LIMIT 5")
for row in cursor.fetchall():
    print(f"{row[1]}: {row[0]} 万股")
conn.close()
import sqlite3
import os

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db", "stock_daily.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 1. 打印各表列名
for table in ["moneyflow", "stk_holdernumber", "bak_basic"]:
    cursor.execute(f"PRAGMA table_info({table})")
    cols = cursor.fetchall()
    print(f"\nTable {table} columns:")
    for col in cols:
        print(f"  {col[1]} ({col[2]})")

# 2. 查询各表样板记录
for table in ["moneyflow", "stk_holdernumber", "bak_basic"]:
    cursor.execute(f"SELECT * FROM {table} LIMIT 1")
    row = cursor.fetchone()
    print(f"\nSample row from {table}:", row)

conn.close()

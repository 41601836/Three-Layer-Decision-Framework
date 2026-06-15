import sqlite3

conn = sqlite3.connect('db/stock_daily.db')

# 查找包含指数的股票代码
cursor = conn.execute("SELECT DISTINCT ts_code FROM stock_list WHERE ts_code LIKE '%.SH' LIMIT 10")
print('上证股票代码示例:')
for row in cursor.fetchall():
    print(row[0])

# 检查是否有指数数据
cursor = conn.execute("SELECT ts_code, COUNT(*) FROM daily_prices WHERE ts_code LIKE '000%.SH' GROUP BY ts_code")
print('\n指数日线数据:')
for row in cursor.fetchall():
    print(f'{row[0]}: {row[1]} 条')

conn.close()
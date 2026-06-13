import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'db', 'stock_daily.db')

if not os.path.exists(DB_PATH):
    print('❌ 数据库文件不存在:', DB_PATH)
    exit(1)

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

print('=== 数据库数据完整性检查 ===\n')

cursor.execute('SELECT COUNT(*) FROM stock_list')
stock_count = cursor.fetchone()[0]
print(f'📋 股票列表: {stock_count:,} 只')

cursor.execute('SELECT COUNT(*) FROM daily_prices')
daily_count = cursor.fetchone()[0]
print(f'📈 行情数据: {daily_count:,} 条')

cursor.execute('SELECT COUNT(DISTINCT ts_code) FROM daily_prices')
price_stock_count = cursor.fetchone()[0]
print(f'📉 有行情的股票: {price_stock_count:,} 只')

cursor.execute('SELECT MIN(trade_date), MAX(trade_date) FROM daily_prices')
dates = cursor.fetchone()
print(f'📅 时间范围: {dates[0]} ~ {dates[1]}')

cursor.execute('SELECT COUNT(*) FROM moneyflow')
moneyflow_count = cursor.fetchone()[0]
print(f'💰 资金流数据: {moneyflow_count:,} 条')

cursor.execute('SELECT COUNT(*) FROM stk_holdernumber')
holder_count = cursor.fetchone()[0]
print(f'👥 股东数据: {holder_count:,} 条')

print('\n=== 完整性检查 ===')

issues = []
if stock_count != price_stock_count:
    issues.append(f'⚠️ 股票列表({stock_count})与行情数据({price_stock_count})数量不一致')

if issues:
    for issue in issues:
        print(issue)
else:
    print('✅ 数据完整')

conn.close()

import sqlite3
import pandas as pd

DB_PATH = "d:/StockAI/db/stock_daily.db"

def fetch_daily_data(ts_code, days=250):
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql(
            f"""SELECT ts_code, trade_date, open, high, low, close, vol, amount
                FROM daily_prices 
                WHERE ts_code = ? 
                ORDER BY trade_date DESC LIMIT {days}""",
            conn,
            params=(ts_code,)
        )
        conn.close()
        if df.empty:
            return None
        df = df.sort_values('trade_date')
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        return df
    except Exception as e:
        print(f"读取数据失败: {e}")
        return None

df = fetch_daily_data('603327.SH')

print("=== 数据基本信息 ===")
print(f"数据总行数: {len(df)}")
print(f"最早日期: {df['trade_date'].min().date()}")
print(f"最晚日期: {df['trade_date'].max().date()}")

print("\n=== 最后20条数据 ===")
last_20 = df.tail(20)
print(f"{'日期':<12} {'成交量':<15}")
print("-" * 27)
for _, row in last_20.iterrows():
    print(f"{row['trade_date'].date():<12} {row['vol']:<15.0f}")

print(f"\n最后20天平均成交量: {last_20['vol'].mean():.0f}")

# 获取流通股本
conn = sqlite3.connect(DB_PATH)
cursor = conn.execute("SELECT float_share FROM daily_basic WHERE ts_code='603327.SH' ORDER BY trade_date DESC LIMIT 1")
float_share = cursor.fetchone()[0]
conn.close()

circ_cap = float_share / 10000  # 亿股
turnover = last_20['vol'].mean() / (circ_cap * 10000)
print(f"\n计算换手率: {last_20['vol'].mean():.0f} / ({circ_cap:.4f} * 10000) = {turnover:.4f}%")
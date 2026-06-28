# -*- coding: utf-8 -*-
"""
补全缺失的数据表：cyq_chips、fina_indicator、hsgt_stock
"""
import os
import sys
import time
import sqlite3
import pandas as pd

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")

sys.path.insert(0, ROOT_DIR)

try:
    from scripts.tokens import TOKEN
    import tushare as ts
    ts.set_token(TOKEN)
    pro = ts.pro_api()
    print("✅ Tushare 初始化成功")
except Exception as e:
    print(f"❌ Tushare 初始化失败: {e}")
    sys.exit(1)


def create_cyq_chips(conn, limit=200):
    """补拉筹码数据"""
    print("\n📥 开始补拉 cyq_chips 表...")
    stocks = pd.read_sql(f'SELECT ts_code FROM stock_list LIMIT {limit}', conn)['ts_code'].tolist()
    trade_date = '20260624'
    count = 0
    
    for code in stocks:
        try:
            df = pro.cyq_chips(ts_code=code, trade_date=trade_date)
            if not df.empty:
                df.to_sql('cyq_chips', conn, if_exists='append', index=False)
                count += 1
                print(f"✅ {code}")
            time.sleep(0.2)
        except Exception as e:
            print(f"❌ {code}: {e}")
    
    print(f"\n✅ cyq_chips 完成，共插入 {count} 条记录")


def create_fina_indicator(conn, limit=200):
    """补拉财务指标"""
    print("\n📥 开始补拉 fina_indicator 表...")
    stocks = pd.read_sql(f'SELECT ts_code FROM stock_list LIMIT {limit}', conn)['ts_code'].tolist()
    count = 0
    
    for code in stocks:
        try:
            df = pro.fina_indicator(ts_code=code, period='20251231')
            if not df.empty:
                df = df[['ts_code', 'report_date', 'roe', 'net_profit_yoy']]
                df.to_sql('fina_indicator', conn, if_exists='append', index=False)
                count += 1
                print(f"✅ {code}")
            time.sleep(0.1)
        except Exception as e:
            print(f"❌ {code}: {e}")
    
    print(f"\n✅ fina_indicator 完成，共插入 {count} 条记录")


def create_hsgt_stock(conn, limit=200):
    """补拉北向资金"""
    print("\n📥 开始补拉 hsgt_stock 表...")
    stocks = pd.read_sql(f'SELECT ts_code FROM stock_list LIMIT {limit}', conn)['ts_code'].tolist()
    count = 0
    
    for code in stocks:
        try:
            df = pro.hsgt_stock(ts_code=code, start_date='20260601', end_date='20260624')
            if not df.empty:
                df = df[['ts_code', 'trade_date', 'north_money']]
                df.to_sql('hsgt_stock', conn, if_exists='append', index=False)
                count += 1
                print(f"✅ {code}")
            time.sleep(0.1)
        except Exception as e:
            print(f"❌ {code}: {e}")
    
    print(f"\n✅ hsgt_stock 完成，共插入 {count} 条记录")


def verify_tables(conn):
    """验证表创建结果"""
    print("\n📊 验证表数据：")
    tables = ['cyq_chips', 'fina_indicator', 'hsgt_stock', 'daily_prices', 'stock_list']
    for table in tables:
        try:
            count = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            print(f"  {table}: {count:,} 条记录")
        except Exception as e:
            print(f"  {table}: ❌ {e}")


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    
    try:
        create_cyq_chips(conn, limit=200)
        create_fina_indicator(conn, limit=200)
        create_hsgt_stock(conn, limit=200)
        verify_tables(conn)
    finally:
        conn.commit()
        conn.close()
    
    print("\n🎉 所有表补全完成！")

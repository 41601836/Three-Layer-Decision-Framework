import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "db", "stock_daily.db")

def init_tables():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. 宏观数据缓存表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS macro_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        indicator_code TEXT NOT NULL,
        indicator_value REAL,
        trade_date TEXT NOT NULL,
        update_time TEXT NOT NULL,
        status TEXT DEFAULT 'valid',
        UNIQUE(indicator_code, trade_date)
    )
    """)

    # 2. 概念板块映射表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS concept_mapping (
        ts_code TEXT NOT NULL,
        concept_name TEXT NOT NULL,
        update_date TEXT NOT NULL,
        PRIMARY KEY(ts_code, concept_name)
    )
    """)

    # 3. 每日板块热力图聚合指标表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sector_daily_stats (
        trade_date TEXT NOT NULL,
        concept_name TEXT NOT NULL,
        stock_count INTEGER,
        avg_pct_chg REAL,
        total_amount REAL,
        net_mf_amount REAL,
        limit_up_count INTEGER,
        PRIMARY KEY(trade_date, concept_name)
    )
    """)

    conn.commit()
    conn.close()
    print("New tables created successfully.")

if __name__ == "__main__":
    init_tables()

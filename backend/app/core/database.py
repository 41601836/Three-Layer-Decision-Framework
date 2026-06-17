import os
import sqlite3

# backend/app/core/database.py -> backend/app/core -> backend/app -> backend -> ROOT_DIR
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")

def get_db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn

def init_tables():
    conn = get_db_conn()
    cursor = conn.cursor()

    # 仅创建表，不清空数据

    # 1. 宏观缓存表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS macro_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        macro_type TEXT NOT NULL,          -- 'overseas' / 'domestic'
        indicator_name TEXT NOT NULL,      -- 'SPX' / 'VIX' / 'PMI' 等
        indicator_value TEXT NOT NULL,
        data_date TEXT NOT NULL,           -- YYYY-MM-DD
        source TEXT DEFAULT 'akshare',
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        updated_at TEXT DEFAULT (datetime('now', 'localtime')),
        UNIQUE(macro_type, indicator_name, data_date)
    );
    """)

    # 2. 概念板块映射表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS concept_mapping (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        concept_name TEXT NOT NULL,
        ts_code TEXT NOT NULL,
        trade_date TEXT NOT NULL,          -- YYYYMMDD
        UNIQUE(concept_name, ts_code, trade_date)
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_concept_name ON concept_mapping(concept_name, trade_date);")

    # 3. 板块每日聚合统计表（热力图核心数据源）
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sector_daily_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT NOT NULL,
        sector_name TEXT NOT NULL,
        pct_chg REAL,
        amount REAL,                       -- 总成交额(亿元)
        net_mf_amount REAL,                -- 主力资金净流入(亿元)
        limit_up_count INTEGER,
        total_stocks INTEGER,
        coverage_rate REAL,                -- 涨停覆盖率(%)
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        UNIQUE(trade_date, sector_name)
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sector_stats_date ON sector_daily_stats(trade_date);")

    # 4. 手动政策配置表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS policy_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        policy_type TEXT NOT NULL,
        policy_status TEXT NOT NULL,       -- '宽松' / '中性' / '收紧'
        effective_date TEXT NOT NULL,
        notes TEXT,
        updated_at TEXT DEFAULT (datetime('now', 'localtime'))
    );
    """)

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_tables()
    print("Tables created")

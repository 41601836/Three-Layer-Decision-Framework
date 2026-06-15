# monday_warfare/db_setup.py
import os
import sqlite3
import logging

logger = logging.getLogger("monday_wave")

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config_loader import get_config

DB_PATH = get_config("database.path", "db/stock_daily.db")

def init_monday_tables():
    """在已有的 stock_daily.db 中增加周一战法所需的表，不影响原有表"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS weekly_prices (
            ts_code TEXT,
            trade_date TEXT,
            open REAL, high REAL, low REAL, close REAL,
            pre_close REAL, change REAL, pct_chg REAL,
            vol REAL, amount REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS moneyflow_daily (
            ts_code TEXT,
            trade_date TEXT,
            buy_elg_vol REAL, sell_elg_vol REAL,
            buy_lg_vol REAL, sell_lg_vol REAL,
            buy_md_vol REAL, sell_md_vol REAL,
            buy_sm_vol REAL, sell_sm_vol REAL,
            net_mf_amount REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS margin_summary (
            trade_date TEXT PRIMARY KEY,
            rzye REAL
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS limit_data (
            trade_date TEXT,
            ts_code TEXT,
            limit_type TEXT,
            PRIMARY KEY (trade_date, ts_code)
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS share_unlock (
            ts_code TEXT,
            float_date TEXT,
            float_ratio REAL,
            PRIMARY KEY (ts_code, float_date)
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pledge_stat (
            ts_code TEXT,
            end_date TEXT,
            pledge_ratio REAL,
            PRIMARY KEY (ts_code, end_date)
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS balancesheet (
            ts_code TEXT,
            end_date TEXT,
            goodwill REAL,
            total_equity REAL,
            PRIMARY KEY (ts_code, end_date)
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS forecast (
            ts_code TEXT,
            end_date TEXT,
            type TEXT,
            p_change_min REAL,
            PRIMARY KEY (ts_code, end_date)
        )
    """)
    
    conn.commit()
    conn.close()
    print("[OK] 周一战法数据库表已就绪。")

if __name__ == '__main__':
    init_monday_tables()
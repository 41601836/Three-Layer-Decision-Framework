# -*- coding: utf-8 -*-
"""
数据库初始化脚本
"""
import sqlite3
import os

DB_PATH = 'db/strategy.db'

def init_db():
    """初始化数据库"""
    os.makedirs('db', exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 创建信号表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            score REAL NOT NULL,
            price REAL NOT NULL,
            stop_loss REAL NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 创建交易记录表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT,
            buy_date TEXT NOT NULL,
            buy_price REAL NOT NULL,
            sell_date TEXT,
            sell_price REAL,
            profit REAL,
            profit_pct REAL,
            hold_days INTEGER,
            status TEXT DEFAULT 'open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 创建策略日志表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS strategy_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            signal_count INTEGER,
            strategy_name TEXT,
            win_rate REAL,
            total_return REAL,
            max_drawdown REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 创建索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_signals_code ON signals(code)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_trades_code ON trades(code)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)')
    
    conn.commit()
    conn.close()
    
    print(f"数据库初始化完成: {DB_PATH}")

if __name__ == '__main__':
    init_db()
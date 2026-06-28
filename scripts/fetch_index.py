# -*- coding: utf-8 -*-
"""
拉取上证指数日线数据并存储到数据库
用于市场模式判断（market_env.py）
"""
import os
import sys
import sqlite3
import logging
from datetime import datetime, timedelta

import pandas as pd
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(ROOT_DIR, 'backend')
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.append(BACKEND_DIR)

import tushare as ts

try:
    from scripts.tokens import TOKEN as TUSHARE_TOKEN
except ImportError:
    try:
        from tokens import TOKEN as TUSHARE_TOKEN
    except ImportError:
        TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

pro = ts.pro_api(TUSHARE_TOKEN)

DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# 上证指数代码
INDEX_CODE = "000001.SH"
INDEX_NAME = "上证指数"


def create_daily_index_table(conn):
    """创建daily_index表"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_index (
            ts_code    TEXT    NOT NULL,   -- 指数代码
            trade_date TEXT    NOT NULL,   -- 交易日期 YYYYMMDD
            open       REAL,              -- 开盘价
            high       REAL,              -- 最高价
            low        REAL,              -- 最低价
            close      REAL,              -- 收盘价
            pct_chg    REAL,              -- 涨跌幅(%)
            vol        REAL,              -- 成交量(手)
            amount     REAL,              -- 成交额(元)
           PRIMARY KEY (ts_code, trade_date)
        )
    """)
    conn.commit()
    log.info("[OK] daily_index 表已创建")


def fetch_index_data(start_date, end_date):
    """使用 Tushare 拉取上证指数日线数据"""
    log.info(f"正在拉取 {INDEX_NAME} 数据: {start_date} ~ {end_date}")
    try:
        df = pro.index_daily(ts_code=INDEX_CODE, start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            # Tushare 指数成交额 amount 是千元，换算成元
            df['amount'] = df['amount'] * 1000
            # 升序排序
            df = df.sort_values('trade_date').reset_index(drop=True)
            log.info(f"成功拉取 {len(df)} 条数据")
            return df
        return None
    except Exception as e:
        log.error(f"拉取失败: {e}")
        return None


def save_to_db(conn, df):
    """保存到数据库"""
    if df is None or df.empty:
        log.warning("[SKIP] 无数据可保存")
        return 0
    
    cursor = conn.cursor()
    inserted = 0
    for _, row in df.iterrows():
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO daily_index 
                (ts_code, trade_date, open, high, low, close, pct_chg, vol, amount)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row["ts_code"],
                row["trade_date"],
                row.get("open"),
                row.get("high"),
                row.get("low"),
                row.get("close"),
                row.get("pct_chg"),
                row.get("vol"),
                row.get("amount"),
            ))
            inserted += 1
        except Exception as e:
            log.error(f"插入失败: {e}")
    
    conn.commit()
    log.info(f"已保存 {inserted} 条数据到 daily_index 表")
    return inserted


def main():
    import argparse
    parser = argparse.ArgumentParser(description="拉取上证指数数据")
    parser.add_argument("--start", default="20250101", help="开始日期 YYYYMMDD")
    parser.add_argument("--end",   default=None, help="结束日期 YYYYMMDD，默认今天")
    args = parser.parse_args()
    
    end_date = args.end or datetime.now().strftime("%Y%m%d")
    
    conn = sqlite3.connect(DB_PATH)
    
    # 创建表
    create_daily_index_table(conn)
    
    # 拉取数据
    df = fetch_index_data(args.start, end_date)
    
    # 保存到数据库
    count = save_to_db(conn, df)
    
    # 验证
    if count > 0:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM daily_index WHERE ts_code = ?", (INDEX_CODE,))
        total = cur.fetchone()[0]
        cur.execute("SELECT MIN(trade_date), MAX(trade_date) FROM daily_index WHERE ts_code = ?", (INDEX_CODE,))
        date_range = cur.fetchone()
        log.info(f"数据库验证: {total} 条数据，日期范围: {date_range[0]} ~ {date_range[1]}")
    
    conn.close()
    print(f"\n✅ 上证指数数据拉取完成！共 {count} 条")


if __name__ == "__main__":
    main()
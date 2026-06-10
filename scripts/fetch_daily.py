#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tushare 全市场数据拉取 v2.4 - 支持增量更新和多线程并行
"""
import os
import sys
import time
import math
import sqlite3
import threading
import pandas as pd
import numpy as np
import tushare as ts
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Optional

# 添加项目根目录到Python路径
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

# 配置日志
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(ROOT_DIR, 'logs', f'fetch_daily_{datetime.now().strftime("%Y%m%d")}.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# 全局配置
DB_PATH = os.path.join(ROOT_DIR, 'db', 'stock_daily.db')
RATE_LIMIT_SLEEP = 0.5  # API调用间隔
DB_LOCK = threading.Lock()

# 初始化Tushare
pro = ts.pro_api()

def init_db(conn: sqlite3.Connection):
    """初始化数据库表"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_list (
            ts_code TEXT PRIMARY KEY,
            name TEXT,
            industry TEXT,
            list_date TEXT
        )
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_prices (
            ts_code TEXT,
            trade_date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            pre_close REAL,
            change REAL,
            pct_chg REAL,
            vol REAL,
            amount REAL,
            adj_factor REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
    """)
    
    # 其他表的初始化...
    
    conn.commit()

def fetch_stock_list(conn: sqlite3.Connection) -> pd.DataFrame:
    """获取股票列表"""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM stock_list")
    count = cursor.fetchone()[0]
    
    if count == 0:
        log.info("从 Tushare 拉取最新股票列表")
        stock_df = pro.stock_basic(exchange='', list_status='L', 
                                  fields='ts_code,symbol,name,industry,list_date')
        stock_df.to_sql('stock_list', conn, if_exists='replace', index=False)
        conn.commit()
    else:
        stock_df = pd.read_sql("SELECT * FROM stock_list", conn)
        
    log.info(f"共 {len(stock_df)} 只股票")
    return stock_df

def get_fetch_range(conn: sqlite3.Connection, ts_code: str, global_start: str, global_end: str) -> Optional[Tuple[str, str]]:
    """计算需要拉取的日期范围"""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT MAX(trade_date) FROM daily_prices WHERE ts_code = ?", 
        (ts_code,)
    )
    last_date = cursor.fetchone()[0]
    
    if not last_date:
        return (global_start, global_end)
    
    # 增量更新，只拉取最新数据
    start_date = (datetime.strptime(last_date, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')
    
    if start_date > global_end:
        return None
        
    return (start_date, global_end)

def fetch_and_save_one(conn: sqlite3.Connection, ts_code: str, name: str,
                       global_start: str, global_end: str, incremental: bool = True) -> int:
    """拉取单只股票数据"""
    try:
        if incremental:
            fetch_range = get_fetch_range(conn, ts_code, global_start, global_end)
            if not fetch_range:
                return 0
            start, end = fetch_range
        else:
            start, end = global_start, global_end
            
        # 拉取日线数据
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end,
                      fields='ts_code,trade_date,open,high,low,close,pre_close,'
                             'change,pct_chg,vol,amount')
        time.sleep(RATE_LIMIT_SLEEP)
        
        if df is None or df.empty:
            return 0
            
        # 保存数据
        with DB_LOCK:
            df.to_sql('daily_prices', conn, if_exists='append', index=False)
            conn.commit()
            
        return len(df)
        
    except Exception as e:
        log.error(f"拉取 {ts_code} {name} 失败: {e}")
        return 0

def batch_fetch(stock_df: pd.DataFrame, start_date: str, end_date: str,
                max_workers: int = 8, incremental: bool = True,
                fetch_money: bool = True, fetch_holder: bool = True,
                **kwargs) -> Dict:
    """批量拉取数据"""
    start_ts = time.time()
    total = len(stock_df)
    done = 0
    new_rows = 0
    skipped = 0
    errors = 0
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    
    log.info(f"批量拉取：共 {total} 只股票，{start_date}~{end_date}，并发 {max_workers}")
    
    def _worker(row):
        nonlocal done, new_rows, skipped, errors
        try:
            n = fetch_and_save_one(conn, row.ts_code, row.name, 
                                  start_date, end_date, incremental)
            done += 1
            if n > 0:
                new_rows += n
                return (row.ts_code, n, None)
            else:
                skipped += 1
                return (row.ts_code, 0, None)
        except Exception as e:
            done += 1
            errors += 1
            return (row.ts_code, 0, str(e))
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, r): r.ts_code for r in stock_df.itertuples(index=False)}
        
        for future in as_completed(futures):
            ts_code, n, err = future.result()
            if err:
                log.error(f"❌ {ts_code} 失败: {err}")
            elif n > 0:
                log.info(f"✅ {ts_code} +{n} 行")
                
            if done % 100 == 0 or done == total:
                elapsed = time.time() - start_ts
                speed = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / speed if speed > 0 else 0
                log.info(f"📊 进度 {done}/{total} ({done/total*100:.1f}%) | 新增 {new_rows} 行 | 跳过 {skipped} | 错误 {errors} | 耗时 {elapsed:.0f}s ETA {eta:.0f}s")
    
    conn.close()
    
    elapsed = time.time() - start_ts
    log.info(f"\n🏁 完成！耗时 {elapsed/60:.1f} 分 | 处理 {total} 只 | 新增 {new_rows} 行 | 跳过 {skipped} | 错误 {errors}")
    
    return {
        'total': total,
        'new_rows': new_rows,
        'skipped': skipped,
        'errors': errors,
        'elapsed': elapsed
    }

def parse_args():
    """解析命令行参数"""
    import argparse
    parser = argparse.ArgumentParser(description="Tushare 全市场数据拉取 v2.4")
    
    today = datetime.now().strftime("%Y%m%d")
    parser.add_argument("--start", default="20200101", help="起始日期 YYYYMMDD")
    parser.add_argument("--end", default=today, help="截止日期 YYYYMMDD")
    parser.add_argument("--workers", type=int, default=8, help="并发线程数")
    parser.add_argument("--refresh-list", action="store_true", help="刷新股票列表")
    parser.add_argument("--code", help="只拉取指定股票代码")
    parser.add_argument("--incremental", action="store_true", help="增量更新模式")
    parser.add_argument("--full-update", action="store_true", help="全量更新模式")
    parser.add_argument("--skip-moneyflow", action="store_true", help="跳过资金流向")
    parser.add_argument("--skip-holder", action="store_true", help="跳过股东户数")
    
    return parser.parse_args()

def main():
    """主函数"""
    args = parse_args()
    
    log.info("=" * 60)
    log.info("  StockAI · 日线数据批量拉取 v2.4")
    log.info(f"  日期范围：{args.start} ~ {args.end}")
    log.info(f"  数据库：{DB_PATH}")
    log.info("=" * 60)
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    init_db(conn)
    
    if args.refresh_list:
        conn.execute("DELETE FROM stock_list")
        conn.commit()
        log.info("🔄 已清空股票列表缓存，将重新从 Tushare 拉取")
    
    stock_df = fetch_stock_list(conn)
    
    if args.code:
        mask = stock_df["ts_code"] == args.code
        stock_df = stock_df[mask] if mask.any() else pd.DataFrame(
            [{"ts_code": args.code, "name": args.code}]
        )
        log.info(f"🔍 调试模式：仅拉取 {args.code}")
    
    conn.close()
    
    # 确定更新模式
    incremental = args.incremental or not args.full_update
    
    result = batch_fetch(
        stock_df,
        args.start, args.end,
        max_workers      = args.workers,
        incremental      = incremental,
        fetch_money      = not args.skip_moneyflow,
        fetch_holder     = not args.skip_holder,
    )
    
    log.info("✅ 全部完成！数据库路径：%s", DB_PATH)

if __name__ == "__main__":
    import threading
    main()

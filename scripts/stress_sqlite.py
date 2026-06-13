# -*- coding: utf-8 -*-
"""
stress_sqlite.py —— SQLite 并发锁专项压测脚本
"""

import os
import sys
import time
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")

# 锁报错统计
lock_errors = 0
lock_errors_lock = threading.Lock()

def increment_error():
    global lock_errors
    with lock_errors_lock:
        lock_errors += 1

# 写入任务：高频向临时表中写入数据
def writer_thread(stop_event):
    conn = sqlite3.connect(DB_PATH, timeout=5)
    # 创建测试表
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS test_stress (id INTEGER PRIMARY KEY, val TEXT, updated_at TEXT)")
        conn.commit()
    except Exception as e:
        print(f"创建测试表出错: {e}")
        
    while not stop_event.is_set():
        try:
            conn.execute("INSERT INTO test_stress (val, updated_at) VALUES (?, datetime('now'))", ("test_val",))
            conn.commit()
            time.sleep(0.01) # 控制高频写入
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                increment_error()
        except Exception as e:
            pass
    
    # 清理
    try:
        conn.execute("DROP TABLE IF EXISTS test_stress")
        conn.commit()
    except:
        pass
    conn.close()

# 读取任务：高频查询日线行情
def reader_thread(stop_event):
    conn = sqlite3.connect(DB_PATH, timeout=5)
    while not stop_event.is_set():
        try:
            # 高频读取
            cursor = conn.execute("SELECT * FROM daily_prices LIMIT 10")
            cursor.fetchall()
            time.sleep(0.005) # 极高频读取
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                increment_error()
        except Exception as e:
            pass
    conn.close()

def run_stress_test(journal_mode="DELETE", duration=10, read_workers=10, write_workers=3):
    global lock_errors
    lock_errors = 0
    
    print(f"\n--- 开始 SQLite 压测 | 模式: {journal_mode} | 持续时间: {duration}秒 ---")
    
    # 设置日志模式
    conn = sqlite3.connect(DB_PATH)
    conn.execute(f"PRAGMA journal_mode={journal_mode};")
    actual_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    conn.close()
    print(f"  数据库实际日志模式: {actual_mode.upper()}")
    
    stop_event = threading.Event()
    threads = []
    
    # 启动写入线程
    for _ in range(write_workers):
        t = threading.Thread(target=writer_thread, args=(stop_event,))
        t.start()
        threads.append(t)
        
    # 启动读取线程
    for _ in range(read_workers):
        t = threading.Thread(target=reader_thread, args=(stop_event,))
        t.start()
        threads.append(t)
        
    # 运行指定时长
    time.sleep(duration)
    
    # 停止并汇合
    stop_event.set()
    for t in threads:
        t.join()
        
    print(f"  压测结束 | 出现 database is locked 报错次数: {lock_errors}")
    return lock_errors

def main():
    print("=" * 60)
    print("  StockAI Funnel SQLite 并发锁性能压测")
    print("=" * 60)
    
    # 1. 测试默认的传统删除模式 (DELETE/TRUNCATE) 下并发锁情况
    errors_delete = run_stress_test(journal_mode="DELETE", duration=8, read_workers=15, write_workers=3)
    
    # 2. 测试开启 WAL (Write-Ahead Logging) 模式下的并发锁情况
    errors_wal = run_stress_test(journal_mode="WAL", duration=8, read_workers=15, write_workers=3)
    
    print("\n" + "=" * 60)
    print("  压测比对结果")
    print("-" * 60)
    print(f"  - DELETE 模式 (传统模式) 锁冲突报错数 : {errors_delete} 次")
    print(f"  - WAL 模式 (预写日志模式) 锁冲突报错数 : {errors_wal} 次")
    print("=" * 60)
    
    # 恢复 WAL 模式以保证最佳性能
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.close()

if __name__ == "__main__":
    main()

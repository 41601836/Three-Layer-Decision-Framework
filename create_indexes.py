"""
create_indexes.py —— 为 StockAI 数据库创建查询优化索引
运行一次即可，之后 pre_screen() SQL 查询速度从 ~54s 降至 <1s
"""
import sqlite3
import time
import sys
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "stock_daily.db")

INDEXES = [
    # daily_prices: pre_screen 的核心过滤字段
    ("idx_dp_trade_date",    "daily_prices",  "trade_date"),
    ("idx_dp_pct_chg",       "daily_prices",  "pct_chg"),
    ("idx_dp_amount",        "daily_prices",  "amount"),
    # 联合索引：trade_date + pct_chg + amount（pre_screen WHERE 全命中）
    ("idx_dp_date_pct_amt",  "daily_prices",  "trade_date, pct_chg, amount"),
    # daily_basic: LEFT JOIN + turnover_rate 过滤
    ("idx_db_trade_date",    "daily_basic",   "trade_date"),
    ("idx_db_ts_date",       "daily_basic",   "ts_code, trade_date"),
    # stock_list: JOIN 字段
    ("idx_sl_ts_code",       "stock_list",    "ts_code"),
    ("idx_sl_list_date",     "stock_list",    "list_date"),
]

def create_indexes():
    print("连接数据库:", DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")

    for idx_name, table, columns in INDEXES:
        sql = f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({columns})"
        print(f"  创建索引: {idx_name} ON {table}({columns}) ...", end=" ", flush=True)
        t = time.time()
        try:
            conn.execute(sql)
            conn.commit()
            print(f"OK ({time.time()-t:.2f}s)")
        except Exception as e:
            print(f"SKIP ({e})")

    # 重建查询计划缓存
    conn.execute("ANALYZE;")
    conn.commit()
    conn.close()
    print("\n全部索引创建完成，pre_screen 查询速度将大幅提升。")

    # 验证：重新跑一次 pre_screen 看速度
    print("\n验证 pre_screen 速度...")
    conn2 = sqlite3.connect(DB_PATH)
    from main import pre_screen
    t = time.time()
    results = pre_screen(conn2, pct_min=0.1, pct_max=9.5, turn_min=0.5)
    elapsed = time.time() - t
    conn2.close()
    print(f"pre_screen 耗时: {elapsed:.3f}s（{len(results)} 只股票）")
    if elapsed < 2.0:
        print("✅ 索引生效，速度正常")
    else:
        print(f"⚠️  仍然较慢（{elapsed:.1f}s），可能数据量极大或磁盘 I/O 瓶颈")

if __name__ == "__main__":
    create_indexes()

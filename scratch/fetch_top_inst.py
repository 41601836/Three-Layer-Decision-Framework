# -*- coding: utf-8 -*-
"""
fetch_top_inst.py —— Tushare 龙虎榜机构净买入数据拉取与入库脚本
================================================================
数据来源：Tushare Pro API  pro.top_inst()
目标表  ：top_inst（SQLite，存于 db/stock_daily.db）

字段说明：
  ts_code    : 股票代码
  trade_date : 交易日期
  exalter    : 营业部/机构名称
  buy        : 买入额（元）
  buy_rate   : 买入额占总成交比例
  sell       : 卖出额（元）
  sell_rate  : 卖出额占总成交比例
  net_buy    : 净买入额（元）, 正=净买入，负=净卖出
  reason     : 上榜原因

拉取策略：
  1. 按交易日逐日拉取（top_inst 只支持单日查询）
  2. 每日限速 0.35s（Tushare 约 200次/分钟）
  3. 断点续传：已入库日期自动跳过
  4. 只保存 net_buy > 0 的机构净买入记录

使用方法：
  python3 scratch/fetch_top_inst.py                    # 默认拉取 2024-01-01 至今
  python3 scratch/fetch_top_inst.py --start 20250101 --end 20251231
  python3 scratch/fetch_top_inst.py --all              # 拉取 2023-01-01 至今
  python3 scratch/fetch_top_inst.py --force            # 强制重拉（覆盖已有数据）
"""

import os
import sys
import time
import sqlite3
import argparse
from datetime import datetime

import pandas as pd
import tushare as ts

# ─── 路径配置 ────────────────────────────────────────────────────────────────
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")
sys.path.insert(0, ROOT_DIR)

# ─── Tushare Token ───────────────────────────────────────────────────────────
try:
    from scripts.tokens import TOKEN as TUSHARE_TOKEN
except ImportError:
    try:
        from tokens import TOKEN as TUSHARE_TOKEN
    except ImportError:
        TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

if not TUSHARE_TOKEN:
    raise RuntimeError("未找到 Tushare Token，请检查 scripts/tokens.py")

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()


# ─── 建表 SQL ────────────────────────────────────────────────────────────────
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS top_inst (
    ts_code    TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    exalter    TEXT,
    buy        REAL,
    buy_rate   REAL,
    sell       REAL,
    sell_rate  REAL,
    net_buy    REAL,
    reason     TEXT,
    PRIMARY KEY (ts_code, trade_date, exalter)
);
"""

CREATE_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_top_inst_date   ON top_inst (trade_date);",
    "CREATE INDEX IF NOT EXISTS idx_top_inst_code   ON top_inst (ts_code);",
    "CREATE INDEX IF NOT EXISTS idx_top_inst_netbuy ON top_inst (trade_date, net_buy DESC);",
]


def init_table(conn):
    """初始化 top_inst 表结构"""
    conn.execute(CREATE_TABLE_SQL)
    for sql in CREATE_INDEX_SQL:
        conn.execute(sql)
    conn.commit()
    print("[DB] top_inst 表初始化完成")


def get_existing_dates(conn):
    """获取已入库的所有交易日（断点续传用）"""
    rows = conn.execute("SELECT DISTINCT trade_date FROM top_inst").fetchall()
    return set(r[0] for r in rows)


def get_trade_dates(conn, start_date, end_date):
    """从本地数据库获取真实交易日列表"""
    # 优先 trade_cal
    try:
        rows = conn.execute(
            "SELECT cal_date FROM trade_cal WHERE is_open=1 AND cal_date BETWEEN ? AND ? ORDER BY cal_date",
            (start_date, end_date)
        ).fetchall()
        if rows:
            return [r[0] for r in rows]
    except Exception:
        pass
    # 降级：从 daily_prices 推断
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM daily_prices WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (start_date, end_date)
    ).fetchall()
    return [r[0] for r in rows]


def fetch_one_day(trade_date: str, retry: int = 3) -> pd.DataFrame:
    """
    拉取单日龙虎榜机构成交明细，最多重试 retry 次。
    只保留 net_buy > 0 的机构净买入记录。
    """
    for attempt in range(retry):
        try:
            df = pro.top_inst(
                trade_date=trade_date,
                fields="ts_code,trade_date,exalter,buy,buy_rate,sell,sell_rate,net_buy,reason"
            )
            if df is None or df.empty:
                return pd.DataFrame()
            # 仅保留机构净买入
            df = df[df["net_buy"] > 0].copy()
            return df
        except Exception as e:
            wait = (attempt + 1) * 2
            print(f"  [WARN] {trade_date} 失败（{attempt+1}/{retry}）: {e}，{wait}s 后重试...")
            time.sleep(wait)
    print(f"  [ERROR] {trade_date} 拉取彻底失败，跳过")
    return pd.DataFrame()


def upsert_records(conn, df: pd.DataFrame) -> int:
    """批量 UPSERT 至 top_inst 表"""
    if df.empty:
        return 0

    rows = []
    for _, row in df.iterrows():
        rows.append((
            str(row.get("ts_code", "")),
            str(row.get("trade_date", "")),
            str(row.get("exalter", "") or ""),
            float(row.get("buy", 0) or 0),
            float(row.get("buy_rate", 0) or 0),
            float(row.get("sell", 0) or 0),
            float(row.get("sell_rate", 0) or 0),
            float(row.get("net_buy", 0) or 0),
            str(row.get("reason", "") or ""),
        ))

    conn.executemany(
        "INSERT OR REPLACE INTO top_inst "
        "(ts_code,trade_date,exalter,buy,buy_rate,sell,sell_rate,net_buy,reason) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        rows
    )
    conn.commit()
    return len(rows)


def run_fetch(start_date: str, end_date: str, force: bool = False):
    """主拉取逻辑"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    init_table(conn)

    trade_dates = get_trade_dates(conn, start_date, end_date)
    if not trade_dates:
        print(f"[WARN] {start_date}~{end_date} 无交易日，请先确保 daily_prices 已入库")
        conn.close()
        return

    existing = get_existing_dates(conn) if not force else set()
    pending  = [d for d in trade_dates if d not in existing]

    print(f"\n{'='*60}")
    print(f"  龙虎榜机构净买入数据拉取任务")
    print(f"{'='*60}")
    print(f"  目标区间  : {start_date} ~ {end_date}")
    print(f"  交易日总数: {len(trade_dates)} 天")
    print(f"  已入库天数: {len(existing)} 天（断点续传跳过）")
    print(f"  待拉取天数: {len(pending)} 天")
    print(f"{'='*60}\n")

    if not pending:
        print("✅ 所有日期已入库，如需强制重拉请加 --force")
        conn.close()
        return

    total_rows = 0
    total_days_with_data = 0

    for i, d in enumerate(pending):
        print(f"  [{i+1:4d}/{len(pending)}] {d} ...", end=" ", flush=True)
        df = fetch_one_day(d)
        if df.empty:
            print("（无机构净买入）")
        else:
            n = upsert_records(conn, df)
            total_rows += n
            total_days_with_data += 1
            print(f"✅ {n} 条")
        time.sleep(0.35)

    # 统计
    total_in_db    = conn.execute("SELECT COUNT(*)               FROM top_inst").fetchone()[0]
    distinct_dates = conn.execute("SELECT COUNT(DISTINCT trade_date) FROM top_inst").fetchone()[0]
    distinct_stocks= conn.execute("SELECT COUNT(DISTINCT ts_code)   FROM top_inst").fetchone()[0]
    avg_per_day    = conn.execute(
        "SELECT AVG(cnt) FROM (SELECT trade_date, COUNT(*) cnt FROM top_inst GROUP BY trade_date)"
    ).fetchone()[0] or 0

    print(f"\n{'='*60}")
    print(f"  拉取完成！")
    print(f"  本次新增记录: {total_rows:,} 条（{total_days_with_data} 个有效交易日）")
    print(f"  数据库总量  : {total_in_db:,} 条")
    print(f"  覆盖交易日  : {distinct_dates} 天")
    print(f"  覆盖股票数  : {distinct_stocks} 只")
    print(f"  日均覆盖股票: {avg_per_day:.1f} 只/日")
    print(f"{'='*60}\n")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="龙虎榜机构净买入数据拉取与入库")
    parser.add_argument("--start", default="20240101", help="起始日期 YYYYMMDD，默认 20240101")
    parser.add_argument("--end",   default=datetime.today().strftime("%Y%m%d"), help="截止日期，默认今日")
    parser.add_argument("--all",   action="store_true", help="拉取 2023-01-01 至今全量历史")
    parser.add_argument("--force", action="store_true", help="强制重拉（忽略断点续传）")
    args = parser.parse_args()

    start = "20230101" if args.all else args.start
    end   = args.end

    print(f"[INIT] Token 已加载，开始拉取 {start}~{end} 龙虎榜机构净买入数据...")
    run_fetch(start, end, force=args.force)


if __name__ == "__main__":
    main()

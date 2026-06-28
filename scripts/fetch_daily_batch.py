# -*- coding: utf-8 -*-
"""
fetch_daily_batch.py —— Tushare 按日期批量拉取全市场数据
方案三：使用 Tushare 批量接口替代逐只拉取，大幅提升数据获取效率
"""

import os
import sys
import time
import logging
import sqlite3
import argparse
from datetime import datetime, timedelta

import pandas as pd
import tushare as ts

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

# ─── 路径配置 ─────────────────────────────────────────────────────────────────
DB_DIR      = os.path.join(ROOT_DIR, "db")
DB_PATH     = os.path.join(DB_DIR, "stock_daily.db")
LOG_DIR     = os.path.join(ROOT_DIR, "logs")
LOG_FILE    = os.path.join(LOG_DIR, f"fetch_daily_batch_{datetime.now():%Y%m%d_%H%M%S}.log")

os.makedirs(DB_DIR,  exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ─── Tushare Token ────────────────────────────────────────────────────────────
try:
    from scripts.tokens import TOKEN as TUSHARE_TOKEN
except ImportError:
    try:
        from tokens import TOKEN as TUSHARE_TOKEN
    except ImportError:
        TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

if not TUSHARE_TOKEN:
    log.error("❌ 未找到 Tushare Token，请检查 scripts/tokens.py 或环境变量 TUSHARE_TOKEN")
    sys.exit(1)

pro = ts.pro_api(TUSHARE_TOKEN)

# ─── 全局参数 ─────────────────────────────────────────────────────────────────
RATE_LIMIT_SLEEP = 0.1
BATCH_SIZE = 5000


def fetch_daily_batch(trade_date: str) -> pd.DataFrame:
    """批量拉取当日全市场日线数据（替代逐只拉取）"""
    try:
        df = pro.daily(trade_date=trade_date)
        time.sleep(RATE_LIMIT_SLEEP)
        if df is not None and not df.empty:
            df["amount"] = df["amount"] * 1000  # 千元转元
            df["adj_factor"] = 1.0
            return df[["ts_code", "trade_date", "open", "high", "low", "close", 
                       "pre_close", "change", "pct_chg", "vol", "amount", "adj_factor"]]
    except Exception as e:
        log.warning("⚠️ 批量拉取 %s 失败：%s", trade_date, e)
    return pd.DataFrame()


def fetch_daily_basic_batch(trade_date: str) -> pd.DataFrame:
    """批量拉取当日全市场 daily_basic 数据"""
    try:
        df = pro.daily_basic(trade_date=trade_date)
        time.sleep(RATE_LIMIT_SLEEP)
        if df is not None and not df.empty:
            return df[["ts_code", "trade_date", "turnover_rate", "volume_ratio",
                       "pe", "pb", "ps", "total_share", "float_share", "free_share",
                       "total_mv", "circ_mv"]]
    except Exception as e:
        log.warning("⚠️ 批量拉取 daily_basic %s 失败：%s", trade_date, e)
    return pd.DataFrame()


def fetch_moneyflow_batch(trade_date: str) -> pd.DataFrame:
    """批量拉取当日全市场资金流向数据"""
    try:
        df = pro.moneyflow(trade_date=trade_date)
        time.sleep(RATE_LIMIT_SLEEP)
        if df is not None and not df.empty:
            return df
    except Exception as e:
        log.warning("⚠️ 批量拉取 moneyflow %s 失败：%s", trade_date, e)
    return pd.DataFrame()


def get_trade_dates(start_date: str, end_date: str) -> list:
    """获取指定日期范围内的交易日列表"""
    try:
        df = pro.trade_cal(start_date=start_date, end_date=end_date, is_open="1")
        return sorted(df["cal_date"].tolist())
    except Exception as e:
        log.error("❌ 获取交易日历失败：%s", e)
        return []


def get_latest_trade_date(conn: sqlite3.Connection) -> str:
    """获取数据库中最新的交易日期"""
    row = conn.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
    return row[0] if row and row[0] else None


def batch_insert(conn: sqlite3.Connection, df: pd.DataFrame, table_name: str, 
                 columns: list, pk_columns: list = None):
    """批量插入数据"""
    if df.empty:
        return 0
    
    if pk_columns:
        placeholders = ", ".join(f"{col}=?" for col in columns)
        update_clause = ", ".join(f"{col}=excluded.{col}" for col in columns if col not in pk_columns)
        sql = f"INSERT OR REPLACE INTO {table_name} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"
    else:
        sql = f"INSERT OR REPLACE INTO {table_name} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"
    
    rows = [tuple(r) for r in df[columns].itertuples(index=False, name=None)]
    
    batch_size = BATCH_SIZE
    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        conn.executemany(sql, batch)
        total += len(batch)
    
    return total


def main(start_date=None, end_date=None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    
    # 确定日期范围
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")
    if start_date is None:
        latest = get_latest_trade_date(conn)
        if latest:
            start_date = (datetime.strptime(latest, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        else:
            start_date = "20260101"
    
    log.info("📅 日期范围：%s ~ %s", start_date, end_date)
    
    # 获取交易日列表
    trade_dates = get_trade_dates(start_date, end_date)
    if not trade_dates:
        log.warning("⚠️ 该时间段内无交易日")
        return
    
    log.info("📋 待拉取交易日：%d 天", len(trade_dates))
    
    total_daily = 0
    total_basic = 0
    total_moneyflow = 0
    
    for idx, trade_date in enumerate(trade_dates, 1):
        log.info(f"🚀 [{idx}/{len(trade_dates)}] 拉取 {trade_date}")
        
        # 批量拉取日线
        daily_df = fetch_daily_batch(trade_date)
        if not daily_df.empty:
            cnt = batch_insert(conn, daily_df, "daily_prices", 
                              ["ts_code", "trade_date", "open", "high", "low", "close", 
                               "pre_close", "change", "pct_chg", "vol", "amount", "adj_factor"])
            total_daily += cnt
            log.info(f"  ✅ 日线: {cnt} 条")
        
        # 批量拉取 daily_basic
        basic_df = fetch_daily_basic_batch(trade_date)
        if not basic_df.empty:
            cnt = batch_insert(conn, basic_df, "daily_basic",
                              ["ts_code", "trade_date", "turnover_rate", "volume_ratio",
                               "pe", "pb", "ps", "total_share", "float_share", "free_share",
                               "total_mv", "circ_mv"])
            total_basic += cnt
            log.info(f"  ✅ daily_basic: {cnt} 条")
        
        # 批量拉取资金流向
        mf_df = fetch_moneyflow_batch(trade_date)
        if not mf_df.empty:
            cnt = batch_insert(conn, mf_df, "moneyflow",
                              ["ts_code", "trade_date", "buy_sm_vol", "buy_sm_amount",
                               "sell_sm_vol", "sell_sm_amount", "buy_md_vol", "buy_md_amount",
                               "sell_md_vol", "sell_md_amount", "buy_lg_vol", "buy_lg_amount",
                               "sell_lg_vol", "sell_lg_amount", "buy_elg_vol", "buy_elg_amount",
                               "sell_elg_vol", "sell_elg_amount", "net_mf_vol", "net_mf_amount"])
            total_moneyflow += cnt
            log.info(f"  ✅ 资金流向: {cnt} 条")
        
        conn.commit()
    
    conn.close()
    
    log.info("=" * 60)
    log.info("📊 批量拉取完成")
    log.info(f"  日线: {total_daily:,} 条")
    log.info(f"  daily_basic: {total_basic:,} 条")
    log.info(f"  资金流向: {total_moneyflow:,} 条")
    log.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tushare 批量拉取全市场数据")
    parser.add_argument("--start", help="开始日期 (格式: YYYYMMDD)")
    parser.add_argument("--end", help="结束日期 (格式: YYYYMMDD)")
    args = parser.parse_args()
    
    main(start_date=args.start, end_date=args.end)
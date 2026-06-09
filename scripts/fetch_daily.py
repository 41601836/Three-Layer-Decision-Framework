# -*- coding: utf-8 -*-
"""
fetch_daily.py —— Tushare 全市场日线 + 资金流向 + 股东户数 + daily_basic + margin + block + mins + bak 批量拉取 & SQLite 存储
v2.3 增强版
"""

import io
import os
import sys
import time
import logging
import sqlite3
import argparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import pandas as pd
import tushare as ts

# ─── 路径配置 ─────────────────────────────────────────────────────────────────
ROOT_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR      = os.path.join(ROOT_DIR, "db")
DB_PATH     = os.path.join(DB_DIR, "stock_daily.db")
LOG_DIR     = os.path.join(ROOT_DIR, "logs")
LOG_FILE    = os.path.join(LOG_DIR, f"fetch_daily_{datetime.now():%Y%m%d_%H%M%S}.log")

os.makedirs(DB_DIR,  exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# Windows 控制台强制 UTF-8
_stdout_utf8 = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(_stdout_utf8),
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

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ─── 全局参数 ─────────────────────────────────────────────────────────────────
RATE_LIMIT_SLEEP = 0.05          # 5000积分可放宽至此
BATCH_ROWS = 8000
DB_LOCK = Lock()
MINS_RECENT_DAYS = 5             # 分钟线保留最近天数


# ═══════════════════════════════════════════════════════════════════════════════
# 数据库初始化
# ═══════════════════════════════════════════════════════════════════════════════
def init_db(conn: sqlite3.Connection):
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.executescript("""
        -- 股票列表
        CREATE TABLE IF NOT EXISTS stock_list (
            ts_code    TEXT PRIMARY KEY,
            name       TEXT,
            area       TEXT,
            industry   TEXT,
            list_date  TEXT,
            market     TEXT,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 日线行情
        CREATE TABLE IF NOT EXISTS daily_prices (
            ts_code    TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open       REAL,
            high       REAL,
            low        REAL,
            close      REAL,
            pre_close  REAL,
            change     REAL,
            pct_chg    REAL,
            vol        REAL,
            amount     REAL,
            adj_factor REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_prices (trade_date);

        -- 主力资金流向
        CREATE TABLE IF NOT EXISTS moneyflow (
            ts_code        TEXT NOT NULL,
            trade_date     TEXT NOT NULL,
            buy_sm_vol     REAL,
            buy_sm_amount  REAL,
            sell_sm_vol    REAL,
            sell_sm_amount REAL,
            buy_md_vol     REAL,
            buy_md_amount  REAL,
            sell_md_vol    REAL,
            sell_md_amount REAL,
            buy_lg_vol     REAL,
            buy_lg_amount  REAL,
            sell_lg_vol    REAL,
            sell_lg_amount REAL,
            buy_elg_vol    REAL,
            buy_elg_amount REAL,
            sell_elg_vol   REAL,
            sell_elg_amount REAL,
            net_mf_vol     REAL,
            net_mf_amount  REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_mf_date ON moneyflow (trade_date);

        -- 股东户数
        CREATE TABLE IF NOT EXISTS stk_holdernumber (
            ts_code    TEXT NOT NULL,
            ann_date   TEXT,
            end_date   TEXT NOT NULL,
            holder_num INTEGER,
            PRIMARY KEY (ts_code, end_date)
        );
        CREATE INDEX IF NOT EXISTS idx_holder_ts ON stk_holdernumber (ts_code);

        -- 日线增量记录
        CREATE TABLE IF NOT EXISTS fetch_log (
            ts_code    TEXT PRIMARY KEY,
            last_start TEXT,
            last_end   TEXT,
            rows_total INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 资金流向增量记录
        CREATE TABLE IF NOT EXISTS moneyflow_log (
            ts_code    TEXT PRIMARY KEY,
            last_start TEXT,
            last_end   TEXT,
            rows_total INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 股东户数拉取记录
        CREATE TABLE IF NOT EXISTS holder_log (
            ts_code      TEXT PRIMARY KEY,
            last_fetched TEXT,
            rows_total   INTEGER DEFAULT 0,
            updated_at   TEXT DEFAULT (datetime('now','localtime'))
        );

        -- ==================== v2.3 新增表 ====================
        CREATE TABLE IF NOT EXISTS daily_basic (
            ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
            turnover_rate REAL, volume_ratio REAL,
            pe REAL, pb REAL, ps REAL,
            total_share REAL, float_share REAL, free_share REAL,
            total_mv REAL, circ_mv REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_db_date ON daily_basic (trade_date);

        CREATE TABLE IF NOT EXISTS margin_detail (
            ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
            rzye REAL, rqye REAL, rzmre REAL, rqyl REAL,
            rzche REAL, rqchl REAL, rqmcl REAL, rzrqye REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_margin_date ON margin_detail (trade_date);

        CREATE TABLE IF NOT EXISTS block_trade (
            ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
            price REAL, vol REAL, amount REAL,
            buyer TEXT, seller TEXT,
            premium REAL,
            PRIMARY KEY (ts_code, trade_date, price, vol, amount)
        );

        CREATE TABLE IF NOT EXISTS stk_mins (
            ts_code TEXT NOT NULL, trade_time TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, vol REAL, amount REAL,
            PRIMARY KEY (ts_code, trade_time)
        );
        CREATE INDEX IF NOT EXISTS idx_mins_time ON stk_mins (trade_time);

        CREATE TABLE IF NOT EXISTS bak_basic (
            ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
            name TEXT, industry TEXT, area TEXT,
            pe REAL, float_share REAL, total_share REAL,
            total_assets REAL, liquid_assets REAL, fixed_assets REAL,
            holder_num REAL,
            list_date TEXT, undp REAL, per_undp REAL,
            rev_yoy REAL, profit_yoy REAL, gpr REAL, npr REAL,
            PRIMARY KEY (ts_code, trade_date)
        );

        -- 新增日志表
        CREATE TABLE IF NOT EXISTS daily_basic_log (
            ts_code TEXT PRIMARY KEY, last_start TEXT, last_end TEXT,
            rows_total INTEGER DEFAULT 0, updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS margin_log (
            ts_code TEXT PRIMARY KEY, last_start TEXT, last_end TEXT,
            rows_total INTEGER DEFAULT 0, updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS block_log (
            ts_code TEXT PRIMARY KEY, last_start TEXT, last_end TEXT,
            rows_total INTEGER DEFAULT 0, updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS mins_log (
            ts_code TEXT PRIMARY KEY, last_fetched TEXT,
            rows_total INTEGER DEFAULT 0, updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS bak_basic_log (
            ts_code TEXT PRIMARY KEY, last_start TEXT, last_end TEXT,
            rows_total INTEGER DEFAULT 0, updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    conn.commit()
    log.info("✅ 数据库初始化完成（含 v2.3 新表）")


# ═══════════════════════════════════════════════════════════════════════════════
# 股票列表获取
# ═══════════════════════════════════════════════════════════════════════════════
def fetch_stock_list(conn: sqlite3.Connection) -> pd.DataFrame:
    cursor = conn.execute("SELECT COUNT(*) FROM stock_list")
    count  = cursor.fetchone()[0]
    if count > 0:
        log.info("📋 从数据库读取股票列表（共 %d 只）", count)
        return pd.read_sql("SELECT ts_code, name FROM stock_list ORDER BY ts_code", conn)

    log.info("🌐 从 Tushare 拉取全量股票列表...")
    frames = []
    for market in ["SSE", "SZSE"]:
        try:
            df = pro.stock_basic(exchange=market, list_status="L",
                                 fields="ts_code,name,area,industry,list_date,market")
            frames.append(df)
            time.sleep(RATE_LIMIT_SLEEP)
        except Exception as e:
            log.warning("⚠️ 获取 %s 股票列表失败：%s", market, e)

    if not frames:
        log.error("❌ 无法获取任何股票列表，退出")
        sys.exit(1)

    stock_df = pd.concat(frames, ignore_index=True).drop_duplicates("ts_code")
    stock_df["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with DB_LOCK:
        stock_df.to_sql("stock_list", conn, if_exists="replace", index=False, method="multi", chunksize=500)
        conn.commit()

    log.info("✅ 股票列表已缓存，共 %d 只", len(stock_df))
    return stock_df[["ts_code", "name"]]


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════════
def _safe(row, field: str):
    val = getattr(row, field, None)
    return None if val is None or (isinstance(val, float) and val != val) else val


def _get_incremental_range(conn, table_name, ts_code, global_start, global_end):
    row = conn.execute(
        f"SELECT MAX(trade_date) FROM {table_name} WHERE ts_code = ?", (ts_code,)
    ).fetchone()
    latest = row[0] if row and row[0] else None
    if latest and latest >= global_end:
        return None
    if latest:
        next_day = (datetime.strptime(latest, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        start = max(next_day, global_start)
    else:
        start = global_start
    return None if start > global_end else (start, global_end)


# ═══════════════════════════════════════════════════════════════════════════════
# 原有拉取函数（日线、资金流向、股东户数）
# ═══════════════════════════════════════════════════════════════════════════════
def get_fetch_range(conn: sqlite3.Connection, ts_code: str,
                    global_start: str, global_end: str):
    """每日线增量范围（使用 daily_prices 表）"""
    row = conn.execute(
        "SELECT MAX(trade_date) FROM daily_prices WHERE ts_code = ?", (ts_code,)
    ).fetchone()
    latest = row[0] if row and row[0] else None
    if latest and latest >= global_end:
        return None
    if latest:
        next_day = (datetime.strptime(latest, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        start = max(next_day, global_start)
    else:
        start = global_start
    return None if start > global_end else (start, global_end)


def fetch_adj_factor(ts_code: str, start: str, end: str) -> dict[str, float]:
    try:
        df = pro.adj_factor(ts_code=ts_code, start_date=start, end_date=end,
                            fields="trade_date,adj_factor")
        time.sleep(RATE_LIMIT_SLEEP)
        if df is not None and not df.empty:
            return dict(zip(df["trade_date"], df["adj_factor"]))
    except Exception as e:
        log.debug("复权因子获取失败 %s: %s", ts_code, e)
    return {}


def fetch_and_save_one(conn: sqlite3.Connection, ts_code: str, name: str,
                       global_start: str, global_end: str) -> int:
    fetch_range = get_fetch_range(conn, ts_code, global_start, global_end)
    if fetch_range is None:
        log.debug("⏭  %s (%s) 日线已最新，跳过", ts_code, name)
        return 0

    start, end = fetch_range
    try:
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end,
                       fields="ts_code,trade_date,open,high,low,close,pre_close,"
                              "change,pct_chg,vol,amount")
        time.sleep(RATE_LIMIT_SLEEP)
    except Exception as e:
        log.warning("⚠️  %s daily() 失败：%s", ts_code, e)
        return 0

    if df is None or df.empty:
        log.debug("🔹 %s [%s~%s] 无新数据", ts_code, start, end)
        return 0

    adj_map = fetch_adj_factor(ts_code, start, end)
    df["adj_factor"] = df["trade_date"].map(adj_map)

    rows = [
        (r.ts_code, r.trade_date,
         r.open, r.high, r.low, r.close, r.pre_close,
         r.change, r.pct_chg, r.vol, r.amount,
         r.adj_factor if pd.notna(r.adj_factor) else None)
        for r in df.itertuples(index=False)
    ]

    with DB_LOCK:
        conn.executemany("""
            INSERT OR REPLACE INTO daily_prices
              (ts_code, trade_date, open, high, low, close, pre_close,
               change, pct_chg, vol, amount, adj_factor)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)
        conn.execute("""
            INSERT OR REPLACE INTO fetch_log
              (ts_code, last_start, last_end, rows_total, updated_at)
            VALUES (?, ?, ?, COALESCE(
                (SELECT rows_total FROM fetch_log WHERE ts_code=?), 0) + ?,
                datetime('now','localtime'))
        """, (ts_code, start, end, ts_code, len(rows)))
        conn.commit()

    return len(rows)


def fetch_moneyflow_one(conn: sqlite3.Connection, ts_code: str,
                        global_start: str, global_end: str) -> int:
    rng = _get_incremental_range(conn, "moneyflow", ts_code, global_start, global_end)
    if rng is None:
        return 0
    start, end = rng

    try:
        df = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=end,
                           fields="ts_code,trade_date,"
                                  "buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,"
                                  "buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,"
                                  "buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,"
                                  "buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,"
                                  "net_mf_vol,net_mf_amount")
        time.sleep(RATE_LIMIT_SLEEP)
    except Exception as e:
        log.debug("moneyflow() 失败 %s: %s", ts_code, e)
        return 0

    if df is None or df.empty:
        return 0

    rows = [
        (r.ts_code, r.trade_date,
         _safe(r,'buy_sm_vol'),   _safe(r,'buy_sm_amount'),
         _safe(r,'sell_sm_vol'),  _safe(r,'sell_sm_amount'),
         _safe(r,'buy_md_vol'),   _safe(r,'buy_md_amount'),
         _safe(r,'sell_md_vol'),  _safe(r,'sell_md_amount'),
         _safe(r,'buy_lg_vol'),   _safe(r,'buy_lg_amount'),
         _safe(r,'sell_lg_vol'),  _safe(r,'sell_lg_amount'),
         _safe(r,'buy_elg_vol'),  _safe(r,'buy_elg_amount'),
         _safe(r,'sell_elg_vol'), _safe(r,'sell_elg_amount'),
         _safe(r,'net_mf_vol'),   _safe(r,'net_mf_amount'))
        for r in df.itertuples(index=False)
    ]

    with DB_LOCK:
        conn.executemany("""
            INSERT OR REPLACE INTO moneyflow
              (ts_code, trade_date,
               buy_sm_vol, buy_sm_amount, sell_sm_vol, sell_sm_amount,
               buy_md_vol, buy_md_amount, sell_md_vol, sell_md_amount,
               buy_lg_vol, buy_lg_amount, sell_lg_vol, sell_lg_amount,
               buy_elg_vol, buy_elg_amount, sell_elg_vol, sell_elg_amount,
               net_mf_vol, net_mf_amount)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)
        conn.execute("""
            INSERT OR REPLACE INTO moneyflow_log
              (ts_code, last_start, last_end, rows_total, updated_at)
            VALUES (?, ?, ?, COALESCE(
                (SELECT rows_total FROM moneyflow_log WHERE ts_code=?), 0) + ?,
                datetime('now','localtime'))
        """, (ts_code, start, end, ts_code, len(rows)))
        conn.commit()

    return len(rows)


def fetch_holder_one(conn: sqlite3.Connection, ts_code: str) -> int:
    row = conn.execute(
        "SELECT last_fetched FROM holder_log WHERE ts_code = ?", (ts_code,)
    ).fetchone()
    today = datetime.now().strftime("%Y%m%d")
    if row and row[0] and row[0] >= today:
        return 0

    try:
        df = pro.stk_holdernumber(ts_code=ts_code,
                                  fields="ts_code,ann_date,end_date,holder_num")
        time.sleep(RATE_LIMIT_SLEEP)
    except Exception as e:
        log.debug("stk_holdernumber() 失败 %s: %s", ts_code, e)
        return 0

    if df is None or df.empty:
        return 0

    rows = [
        (r.ts_code,
         r.ann_date if pd.notna(r.ann_date) else None,
         r.end_date,
         int(r.holder_num) if pd.notna(r.holder_num) else None)
        for r in df.itertuples(index=False)
    ]

    with DB_LOCK:
        conn.executemany("""
            INSERT OR REPLACE INTO stk_holdernumber
              (ts_code, ann_date, end_date, holder_num)
            VALUES (?,?,?,?)
        """, rows)
        conn.execute("""
            INSERT OR REPLACE INTO holder_log
              (ts_code, last_fetched, rows_total, updated_at)
            VALUES (?, ?, COALESCE(
                (SELECT rows_total FROM holder_log WHERE ts_code=?), 0) + ?,
                datetime('now','localtime'))
        """, (ts_code, today, ts_code, len(rows)))
        conn.commit()

    return len(rows)


# ═══════════════════════════════════════════════════════════════════════════════
# v2.3 新增拉取函数
# ═══════════════════════════════════════════════════════════════════════════════
def fetch_daily_basic_one(conn, ts_code, global_start, global_end):
    rng = _get_incremental_range(conn, "daily_basic", ts_code, global_start, global_end)
    if rng is None:
        return 0
    start, end = rng
    try:
        df = pro.daily_basic(ts_code=ts_code, start_date=start, end_date=end,
                             fields="ts_code,trade_date,turnover_rate,volume_ratio,"
                                    "pe,pb,ps,total_share,float_share,free_share,total_mv,circ_mv")
        time.sleep(RATE_LIMIT_SLEEP)
    except Exception as e:
        log.debug("daily_basic 失败 %s: %s", ts_code, e)
        return 0
    if df is None or df.empty:
        return 0
    rows = [(r.ts_code, r.trade_date,
             _safe(r,'turnover_rate'), _safe(r,'volume_ratio'),
             _safe(r,'pe'), _safe(r,'pb'), _safe(r,'ps'),
             _safe(r,'total_share'), _safe(r,'float_share'), _safe(r,'free_share'),
             _safe(r,'total_mv'), _safe(r,'circ_mv')) for r in df.itertuples(index=False)]
    with DB_LOCK:
        conn.executemany("INSERT OR REPLACE INTO daily_basic VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.execute("INSERT OR REPLACE INTO daily_basic_log (ts_code,last_start,last_end,rows_total,updated_at) VALUES (?,?,?,COALESCE((SELECT rows_total FROM daily_basic_log WHERE ts_code=?),0)+?,datetime('now','localtime'))",
                     (ts_code, start, end, ts_code, len(rows)))
        conn.commit()
    return len(rows)


def fetch_margin_one(conn, ts_code, global_start, global_end):
    rng = _get_incremental_range(conn, "margin_detail", ts_code, global_start, global_end)
    if rng is None:
        return 0
    start, end = rng
    try:
        df = pro.margin_detail(ts_code=ts_code, start_date=start, end_date=end,
                               fields="ts_code,trade_date,rzye,rqye,rzmre,rqyl,rzche,rqchl,rqmcl,rzrqye")
        time.sleep(RATE_LIMIT_SLEEP)
    except Exception as e:
        log.debug("margin_detail 失败 %s: %s", ts_code, e)
        return 0
    if df is None or df.empty:
        return 0
    rows = [(r.ts_code, r.trade_date,
             _safe(r,'rzye'), _safe(r,'rqye'), _safe(r,'rzmre'), _safe(r,'rqyl'),
             _safe(r,'rzche'), _safe(r,'rqchl'), _safe(r,'rqmcl'), _safe(r,'rzrqye'))
            for r in df.itertuples(index=False)]
    with DB_LOCK:
        conn.executemany("INSERT OR REPLACE INTO margin_detail VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        conn.execute("INSERT OR REPLACE INTO margin_log (ts_code,last_start,last_end,rows_total,updated_at) VALUES (?,?,?,COALESCE((SELECT rows_total FROM margin_log WHERE ts_code=?),0)+?,datetime('now','localtime'))",
                     (ts_code, start, end, ts_code, len(rows)))
        conn.commit()
    return len(rows)


def fetch_block_trade_one(conn, ts_code, global_start, global_end):
    rng = _get_incremental_range(conn, "block_trade", ts_code, global_start, global_end)
    if rng is None:
        return 0
    start, end = rng
    try:
        df = pro.block_trade(ts_code=ts_code, start_date=start, end_date=end,
                             fields="ts_code,trade_date,price,vol,amount,buyer,seller")
        time.sleep(RATE_LIMIT_SLEEP)
    except Exception as e:
        log.debug("block_trade 失败 %s: %s", ts_code, e)
        return 0
    if df is None or df.empty:
        return 0
    rows = [(r.ts_code, r.trade_date,
             _safe(r,'price'), _safe(r,'vol'), _safe(r,'amount'),
             _safe(r,'buyer'), _safe(r,'seller'), None)
            for r in df.itertuples(index=False)]
    with DB_LOCK:
        conn.executemany("INSERT OR REPLACE INTO block_trade VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.execute("INSERT OR REPLACE INTO block_log (ts_code,last_start,last_end,rows_total,updated_at) VALUES (?,?,?,COALESCE((SELECT rows_total FROM block_log WHERE ts_code=?),0)+?,datetime('now','localtime'))",
                     (ts_code, start, end, ts_code, len(rows)))
        conn.commit()
    return len(rows)


def fetch_bak_basic_one(conn, ts_code, global_start, global_end):
    rng = _get_incremental_range(conn, "bak_basic", ts_code, global_start, global_end)
    if rng is None:
        return 0
    start, end = rng
    try:
        df = pro.bak_basic(ts_code=ts_code, start_date=start, end_date=end,
                           fields="ts_code,trade_date,name,industry,area,pe,float_share,"
                                  "total_share,total_assets,liquid_assets,fixed_assets,"
                                  "holder_num,list_date,undp,per_undp,rev_yoy,profit_yoy,gpr,npr")
        time.sleep(RATE_LIMIT_SLEEP)
    except Exception as e:
        log.debug("bak_basic 失败 %s: %s", ts_code, e)
        return 0
    if df is None or df.empty:
        return 0

    # 确保必要字段存在
    required_fields = ['ts_code', 'trade_date']
    if not all(f in df.columns for f in required_fields):
        log.debug("bak_basic 缺少关键字段: %s", df.columns.tolist())
        return 0

    # 过滤掉 trade_date 为 NA 的行
    df = df.dropna(subset=['trade_date'])

    rows = []
    for r in df.itertuples(index=False):
        trade_date = r.trade_date
        if pd.isna(trade_date) or not trade_date:
            continue
        rows.append((
            r.ts_code, trade_date,
            _safe(r,'name'), _safe(r,'industry'), _safe(r,'area'),
            _safe(r,'pe'), _safe(r,'float_share'), _safe(r,'total_share'),
            _safe(r,'total_assets'), _safe(r,'liquid_assets'), _safe(r,'fixed_assets'),
            _safe(r,'holder_num'), _safe(r,'list_date'), _safe(r,'undp'),
            _safe(r,'per_undp'), _safe(r,'rev_yoy'), _safe(r,'profit_yoy'),
            _safe(r,'gpr'), _safe(r,'npr')
        ))
    if not rows:
        return 0

    with DB_LOCK:
        conn.executemany("INSERT OR REPLACE INTO bak_basic VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.execute("INSERT OR REPLACE INTO bak_basic_log (ts_code,last_start,last_end,rows_total,updated_at) VALUES (?,?,?,COALESCE((SELECT rows_total FROM bak_basic_log WHERE ts_code=?),0)+?,datetime('now','localtime'))",
                     (ts_code, start, end, ts_code, len(rows)))
        conn.commit()
    return len(rows)


def fetch_mins_recent(conn, ts_code, days=5):
    today = datetime.now()
    start_d = (today - timedelta(days=days)).strftime("%Y%m%d")
    end_d   = today.strftime("%Y%m%d")
    row = conn.execute("SELECT last_fetched FROM mins_log WHERE ts_code = ?", (ts_code,)).fetchone()
    if row and row[0] == end_d:
        return 0
    all_data = []
    cur = datetime.strptime(start_d, "%Y%m%d")
    while cur.strftime("%Y%m%d") <= end_d:
        date_str = cur.strftime("%Y%m%d")
        try:
            df = pro.stk_mins(ts_code=ts_code, trade_date=date_str, freq='5min',
                              fields="ts_code,trade_time,open,high,low,close,vol,amount")
            time.sleep(RATE_LIMIT_SLEEP)
            if df is not None and not df.empty:
                all_data.append(df)
        except Exception as e:
            log.debug("stk_mins 失败 %s %s: %s", ts_code, date_str, e)
        cur += timedelta(days=1)
    if not all_data:
        return 0
    df_mins = pd.concat(all_data, ignore_index=True)
    rows = [(r.ts_code, r.trade_time,
             r.open, r.high, r.low, r.close, r.vol, r.amount)
            for r in df_mins.itertuples(index=False)]
    with DB_LOCK:
        conn.execute("DELETE FROM stk_mins WHERE ts_code = ? AND trade_time >= ?", (ts_code, start_d))
        conn.executemany("INSERT OR REPLACE INTO stk_mins VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.execute("INSERT OR REPLACE INTO mins_log (ts_code,last_fetched,rows_total,updated_at) VALUES (?,?,?,datetime('now','localtime'))",
                     (ts_code, end_d, len(rows)))
        conn.commit()
    return len(rows)


# ═══════════════════════════════════════════════════════════════════════════════
# 批量拉取主函数
# ═══════════════════════════════════════════════════════════════════════════════
def batch_fetch(stock_df, global_start, global_end, max_workers=1,
                fetch_money=True, fetch_holder=True,
                fetch_daily_basic=True, fetch_margin=True,
                fetch_block=True, fetch_bak=True, fetch_mins=True):
    total = len(stock_df)
    done = 0; skipped = 0; errors = 0; new_rows = 0
    start_ts = time.time()

    modes = ["日线"]
    if fetch_money: modes.append("资金流向")
    if fetch_holder: modes.append("股东户数")
    if fetch_daily_basic: modes.append("daily_basic")
    if fetch_margin: modes.append("融资融券")
    if fetch_block: modes.append("大宗交易")
    if fetch_bak: modes.append("bak_basic")
    if fetch_mins: modes.append("分钟线(近5天)")

    def _worker(row):
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        try:
            n = fetch_and_save_one(conn, row.ts_code, row.name, global_start, global_end)
            n += fetch_moneyflow_one(conn, row.ts_code, global_start, global_end) if fetch_money else 0
            n += fetch_holder_one(conn, row.ts_code) if fetch_holder else 0
            n += fetch_daily_basic_one(conn, row.ts_code, global_start, global_end) if fetch_daily_basic else 0
            n += fetch_margin_one(conn, row.ts_code, global_start, global_end) if fetch_margin else 0
            n += fetch_block_trade_one(conn, row.ts_code, global_start, global_end) if fetch_block else 0
            n += fetch_bak_basic_one(conn, row.ts_code, global_start, global_end) if fetch_bak else 0
            if fetch_mins:
                n += fetch_mins_recent(conn, row.ts_code, days=MINS_RECENT_DAYS)
            return row.ts_code, n, None
        except Exception as e:
            return row.ts_code, 0, str(e)
        finally:
            conn.close()

    log.info("🚀 批量拉取 [%s]：共 %d 只股票，%s~%s，并发 %d",
             " + ".join(modes), total, global_start, global_end, max_workers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, r): r.ts_code for r in stock_df.itertuples(index=False)}
        for future in as_completed(futures):
            ts_code, n, err = future.result()
            done += 1
            if err:
                errors += 1
                log.error("❌ [%d/%d] %s 失败：%s", done, total, ts_code, err)
            elif n == 0:
                skipped += 1
            else:
                new_rows += n
                log.info("✅ [%d/%d] %-12s +%d 行", done, total, ts_code, n)

            if done % 100 == 0 or done == total:
                elapsed = time.time() - start_ts
                speed = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / speed if speed > 0 else 0
                log.info("📊 进度 %d/%d (%.1f%%) | 新增 %d 行 | 跳过 %d | 错误 %d | 耗时 %.0fs ETA %.0fs",
                         done, total, done/total*100, new_rows, skipped, errors, elapsed, eta)

    elapsed = time.time() - start_ts
    log.info("\n🏁 完成！耗时 %.1f 分 | 处理 %d 只 | 新增 %d 行 | 跳过 %d | 错误 %d",
             elapsed/60, total, new_rows, skipped, errors)


# ═══════════════════════════════════════════════════════════════════════════════
# 命令行入口
# ═══════════════════════════════════════════════════════════════════════════════
def parse_args():
    parser = argparse.ArgumentParser(description="Tushare 全市场数据拉取 v2.3")
    today = datetime.now().strftime("%Y%m%d")
    parser.add_argument("--start", default="20200101", help="起始日期 YYYYMMDD")
    parser.add_argument("--end", default=today, help="截止日期 YYYYMMDD")
    parser.add_argument("--workers", type=int, default=1, help="并发线程数")
    parser.add_argument("--refresh-list", action="store_true", help="强制刷新股票列表缓存")
    parser.add_argument("--code", default=None, help="只拉取指定股票（调试）")
    parser.add_argument("--skip-moneyflow", action="store_true", help="跳过资金流向")
    parser.add_argument("--skip-holder", action="store_true", help="跳过股东户数")
    parser.add_argument("--skip-daily-basic", action="store_true", help="跳过 daily_basic")
    parser.add_argument("--skip-margin", action="store_true", help="跳过融资融券")
    parser.add_argument("--skip-block", action="store_true", help="跳过大宗交易")
    parser.add_argument("--skip-bak", action="store_true", help="跳过 bak_basic")
    parser.add_argument("--skip-mins", action="store_true", help="跳过分钟线拉取")
    return parser.parse_args()


def main():
    args = parse_args()

    log.info("=" * 60)
    log.info("  StockAI · 日线数据批量拉取 v2.3")
    log.info("  日期范围：%s ~ %s", args.start, args.end)
    log.info("  数据库：%s", DB_PATH)
    log.info("  日志：%s", LOG_FILE)
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
        log.info("🔍 调试模式：仅拉取 %s", args.code)

    conn.close()

    batch_fetch(
        stock_df,
        args.start, args.end,
        max_workers      = args.workers,
        fetch_money      = not args.skip_moneyflow,
        fetch_holder     = not args.skip_holder,
        fetch_daily_basic= not args.skip_daily_basic,
        fetch_margin     = not args.skip_margin,
        fetch_block      = not args.skip_block,
        fetch_bak        = not args.skip_bak,
        fetch_mins       = not args.skip_mins,
    )

    log.info("✅ 全部完成！数据库路径：%s", DB_PATH)


if __name__ == "__main__":
    main()# -*- coding: utf-8 -*-
"""
fetch_daily.py —— Tushare 全市场日线 + 资金流向 + 股东户数 + daily_basic + margin + block + mins + bak 批量拉取 & SQLite 存储
v2.3 增强版
"""

import io
import os
import sys
import time
import logging
import sqlite3
import argparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import pandas as pd
import tushare as ts

# ─── 路径配置 ─────────────────────────────────────────────────────────────────
ROOT_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR      = os.path.join(ROOT_DIR, "db")
DB_PATH     = os.path.join(DB_DIR, "stock_daily.db")
LOG_DIR     = os.path.join(ROOT_DIR, "logs")
LOG_FILE    = os.path.join(LOG_DIR, f"fetch_daily_{datetime.now():%Y%m%d_%H%M%S}.log")

os.makedirs(DB_DIR,  exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# Windows 控制台强制 UTF-8
_stdout_utf8 = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(_stdout_utf8),
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

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ─── 全局参数 ─────────────────────────────────────────────────────────────────
RATE_LIMIT_SLEEP = 0.05          # 5000积分可放宽至此
BATCH_ROWS = 8000
DB_LOCK = Lock()
MINS_RECENT_DAYS = 5             # 分钟线保留最近天数


# ═══════════════════════════════════════════════════════════════════════════════
# 数据库初始化
# ═══════════════════════════════════════════════════════════════════════════════
def init_db(conn: sqlite3.Connection):
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.executescript("""
        -- 股票列表
        CREATE TABLE IF NOT EXISTS stock_list (
            ts_code    TEXT PRIMARY KEY,
            name       TEXT,
            area       TEXT,
            industry   TEXT,
            list_date  TEXT,
            market     TEXT,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 日线行情
        CREATE TABLE IF NOT EXISTS daily_prices (
            ts_code    TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open       REAL,
            high       REAL,
            low        REAL,
            close      REAL,
            pre_close  REAL,
            change     REAL,
            pct_chg    REAL,
            vol        REAL,
            amount     REAL,
            adj_factor REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_prices (trade_date);

        -- 主力资金流向
        CREATE TABLE IF NOT EXISTS moneyflow (
            ts_code        TEXT NOT NULL,
            trade_date     TEXT NOT NULL,
            buy_sm_vol     REAL,
            buy_sm_amount  REAL,
            sell_sm_vol    REAL,
            sell_sm_amount REAL,
            buy_md_vol     REAL,
            buy_md_amount  REAL,
            sell_md_vol    REAL,
            sell_md_amount REAL,
            buy_lg_vol     REAL,
            buy_lg_amount  REAL,
            sell_lg_vol    REAL,
            sell_lg_amount REAL,
            buy_elg_vol    REAL,
            buy_elg_amount REAL,
            sell_elg_vol   REAL,
            sell_elg_amount REAL,
            net_mf_vol     REAL,
            net_mf_amount  REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_mf_date ON moneyflow (trade_date);

        -- 股东户数
        CREATE TABLE IF NOT EXISTS stk_holdernumber (
            ts_code    TEXT NOT NULL,
            ann_date   TEXT,
            end_date   TEXT NOT NULL,
            holder_num INTEGER,
            PRIMARY KEY (ts_code, end_date)
        );
        CREATE INDEX IF NOT EXISTS idx_holder_ts ON stk_holdernumber (ts_code);

        -- 日线增量记录
        CREATE TABLE IF NOT EXISTS fetch_log (
            ts_code    TEXT PRIMARY KEY,
            last_start TEXT,
            last_end   TEXT,
            rows_total INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 资金流向增量记录
        CREATE TABLE IF NOT EXISTS moneyflow_log (
            ts_code    TEXT PRIMARY KEY,
            last_start TEXT,
            last_end   TEXT,
            rows_total INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 股东户数拉取记录
        CREATE TABLE IF NOT EXISTS holder_log (
            ts_code      TEXT PRIMARY KEY,
            last_fetched TEXT,
            rows_total   INTEGER DEFAULT 0,
            updated_at   TEXT DEFAULT (datetime('now','localtime'))
        );

        -- ==================== v2.3 新增表 ====================
        CREATE TABLE IF NOT EXISTS daily_basic (
            ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
            turnover_rate REAL, volume_ratio REAL,
            pe REAL, pb REAL, ps REAL,
            total_share REAL, float_share REAL, free_share REAL,
            total_mv REAL, circ_mv REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_db_date ON daily_basic (trade_date);

        CREATE TABLE IF NOT EXISTS margin_detail (
            ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
            rzye REAL, rqye REAL, rzmre REAL, rqyl REAL,
            rzche REAL, rqchl REAL, rqmcl REAL, rzrqye REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_margin_date ON margin_detail (trade_date);

        CREATE TABLE IF NOT EXISTS block_trade (
            ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
            price REAL, vol REAL, amount REAL,
            buyer TEXT, seller TEXT,
            premium REAL,
            PRIMARY KEY (ts_code, trade_date, price, vol, amount)
        );

        CREATE TABLE IF NOT EXISTS stk_mins (
            ts_code TEXT NOT NULL, trade_time TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, vol REAL, amount REAL,
            PRIMARY KEY (ts_code, trade_time)
        );
        CREATE INDEX IF NOT EXISTS idx_mins_time ON stk_mins (trade_time);

        CREATE TABLE IF NOT EXISTS bak_basic (
            ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
            name TEXT, industry TEXT, area TEXT,
            pe REAL, float_share REAL, total_share REAL,
            total_assets REAL, liquid_assets REAL, fixed_assets REAL,
            holder_num REAL,
            list_date TEXT, undp REAL, per_undp REAL,
            rev_yoy REAL, profit_yoy REAL, gpr REAL, npr REAL,
            PRIMARY KEY (ts_code, trade_date)
        );

        -- 新增日志表
        CREATE TABLE IF NOT EXISTS daily_basic_log (
            ts_code TEXT PRIMARY KEY, last_start TEXT, last_end TEXT,
            rows_total INTEGER DEFAULT 0, updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS margin_log (
            ts_code TEXT PRIMARY KEY, last_start TEXT, last_end TEXT,
            rows_total INTEGER DEFAULT 0, updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS block_log (
            ts_code TEXT PRIMARY KEY, last_start TEXT, last_end TEXT,
            rows_total INTEGER DEFAULT 0, updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS mins_log (
            ts_code TEXT PRIMARY KEY, last_fetched TEXT,
            rows_total INTEGER DEFAULT 0, updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS bak_basic_log (
            ts_code TEXT PRIMARY KEY, last_start TEXT, last_end TEXT,
            rows_total INTEGER DEFAULT 0, updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    conn.commit()
    log.info("✅ 数据库初始化完成（含 v2.3 新表）")


# ═══════════════════════════════════════════════════════════════════════════════
# 股票列表获取
# ═══════════════════════════════════════════════════════════════════════════════
def fetch_stock_list(conn: sqlite3.Connection) -> pd.DataFrame:
    cursor = conn.execute("SELECT COUNT(*) FROM stock_list")
    count  = cursor.fetchone()[0]
    if count > 0:
        log.info("📋 从数据库读取股票列表（共 %d 只）", count)
        return pd.read_sql("SELECT ts_code, name FROM stock_list ORDER BY ts_code", conn)

    log.info("🌐 从 Tushare 拉取全量股票列表...")
    frames = []
    for market in ["SSE", "SZSE"]:
        try:
            df = pro.stock_basic(exchange=market, list_status="L",
                                 fields="ts_code,name,area,industry,list_date,market")
            frames.append(df)
            time.sleep(RATE_LIMIT_SLEEP)
        except Exception as e:
            log.warning("⚠️ 获取 %s 股票列表失败：%s", market, e)

    if not frames:
        log.error("❌ 无法获取任何股票列表，退出")
        sys.exit(1)

    stock_df = pd.concat(frames, ignore_index=True).drop_duplicates("ts_code")
    stock_df["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with DB_LOCK:
        stock_df.to_sql("stock_list", conn, if_exists="replace", index=False, method="multi", chunksize=500)
        conn.commit()

    log.info("✅ 股票列表已缓存，共 %d 只", len(stock_df))
    return stock_df[["ts_code", "name"]]


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════════
def _safe(row, field: str):
    val = getattr(row, field, None)
    return None if val is None or (isinstance(val, float) and val != val) else val


def _get_incremental_range(conn, table_name, ts_code, global_start, global_end):
    row = conn.execute(
        f"SELECT MAX(trade_date) FROM {table_name} WHERE ts_code = ?", (ts_code,)
    ).fetchone()
    latest = row[0] if row and row[0] else None
    if latest and latest >= global_end:
        return None
    if latest:
        next_day = (datetime.strptime(latest, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        start = max(next_day, global_start)
    else:
        start = global_start
    return None if start > global_end else (start, global_end)


# ═══════════════════════════════════════════════════════════════════════════════
# 原有拉取函数（日线、资金流向、股东户数）
# ═══════════════════════════════════════════════════════════════════════════════
def get_fetch_range(conn: sqlite3.Connection, ts_code: str,
                    global_start: str, global_end: str):
    """每日线增量范围（使用 daily_prices 表）"""
    row = conn.execute(
        "SELECT MAX(trade_date) FROM daily_prices WHERE ts_code = ?", (ts_code,)
    ).fetchone()
    latest = row[0] if row and row[0] else None
    if latest and latest >= global_end:
        return None
    if latest:
        next_day = (datetime.strptime(latest, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        start = max(next_day, global_start)
    else:
        start = global_start
    return None if start > global_end else (start, global_end)


def fetch_adj_factor(ts_code: str, start: str, end: str) -> dict[str, float]:
    try:
        df = pro.adj_factor(ts_code=ts_code, start_date=start, end_date=end,
                            fields="trade_date,adj_factor")
        time.sleep(RATE_LIMIT_SLEEP)
        if df is not None and not df.empty:
            return dict(zip(df["trade_date"], df["adj_factor"]))
    except Exception as e:
        log.debug("复权因子获取失败 %s: %s", ts_code, e)
    return {}


def fetch_and_save_one(conn: sqlite3.Connection, ts_code: str, name: str,
                       global_start: str, global_end: str) -> int:
    fetch_range = get_fetch_range(conn, ts_code, global_start, global_end)
    if fetch_range is None:
        log.debug("⏭  %s (%s) 日线已最新，跳过", ts_code, name)
        return 0

    start, end = fetch_range
    try:
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end,
                       fields="ts_code,trade_date,open,high,low,close,pre_close,"
                              "change,pct_chg,vol,amount")
        time.sleep(RATE_LIMIT_SLEEP)
    except Exception as e:
        log.warning("⚠️  %s daily() 失败：%s", ts_code, e)
        return 0

    if df is None or df.empty:
        log.debug("🔹 %s [%s~%s] 无新数据", ts_code, start, end)
        return 0

    adj_map = fetch_adj_factor(ts_code, start, end)
    df["adj_factor"] = df["trade_date"].map(adj_map)

    rows = [
        (r.ts_code, r.trade_date,
         r.open, r.high, r.low, r.close, r.pre_close,
         r.change, r.pct_chg, r.vol, r.amount,
         r.adj_factor if pd.notna(r.adj_factor) else None)
        for r in df.itertuples(index=False)
    ]

    with DB_LOCK:
        conn.executemany("""
            INSERT OR REPLACE INTO daily_prices
              (ts_code, trade_date, open, high, low, close, pre_close,
               change, pct_chg, vol, amount, adj_factor)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)
        conn.execute("""
            INSERT OR REPLACE INTO fetch_log
              (ts_code, last_start, last_end, rows_total, updated_at)
            VALUES (?, ?, ?, COALESCE(
                (SELECT rows_total FROM fetch_log WHERE ts_code=?), 0) + ?,
                datetime('now','localtime'))
        """, (ts_code, start, end, ts_code, len(rows)))
        conn.commit()

    return len(rows)


def fetch_moneyflow_one(conn: sqlite3.Connection, ts_code: str,
                        global_start: str, global_end: str) -> int:
    rng = _get_incremental_range(conn, "moneyflow", ts_code, global_start, global_end)
    if rng is None:
        return 0
    start, end = rng

    try:
        df = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=end,
                           fields="ts_code,trade_date,"
                                  "buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,"
                                  "buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,"
                                  "buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,"
                                  "buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,"
                                  "net_mf_vol,net_mf_amount")
        time.sleep(RATE_LIMIT_SLEEP)
    except Exception as e:
        log.debug("moneyflow() 失败 %s: %s", ts_code, e)
        return 0

    if df is None or df.empty:
        return 0

    rows = [
        (r.ts_code, r.trade_date,
         _safe(r,'buy_sm_vol'),   _safe(r,'buy_sm_amount'),
         _safe(r,'sell_sm_vol'),  _safe(r,'sell_sm_amount'),
         _safe(r,'buy_md_vol'),   _safe(r,'buy_md_amount'),
         _safe(r,'sell_md_vol'),  _safe(r,'sell_md_amount'),
         _safe(r,'buy_lg_vol'),   _safe(r,'buy_lg_amount'),
         _safe(r,'sell_lg_vol'),  _safe(r,'sell_lg_amount'),
         _safe(r,'buy_elg_vol'),  _safe(r,'buy_elg_amount'),
         _safe(r,'sell_elg_vol'), _safe(r,'sell_elg_amount'),
         _safe(r,'net_mf_vol'),   _safe(r,'net_mf_amount'))
        for r in df.itertuples(index=False)
    ]

    with DB_LOCK:
        conn.executemany("""
            INSERT OR REPLACE INTO moneyflow
              (ts_code, trade_date,
               buy_sm_vol, buy_sm_amount, sell_sm_vol, sell_sm_amount,
               buy_md_vol, buy_md_amount, sell_md_vol, sell_md_amount,
               buy_lg_vol, buy_lg_amount, sell_lg_vol, sell_lg_amount,
               buy_elg_vol, buy_elg_amount, sell_elg_vol, sell_elg_amount,
               net_mf_vol, net_mf_amount)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)
        conn.execute("""
            INSERT OR REPLACE INTO moneyflow_log
              (ts_code, last_start, last_end, rows_total, updated_at)
            VALUES (?, ?, ?, COALESCE(
                (SELECT rows_total FROM moneyflow_log WHERE ts_code=?), 0) + ?,
                datetime('now','localtime'))
        """, (ts_code, start, end, ts_code, len(rows)))
        conn.commit()

    return len(rows)


def fetch_holder_one(conn: sqlite3.Connection, ts_code: str) -> int:
    row = conn.execute(
        "SELECT last_fetched FROM holder_log WHERE ts_code = ?", (ts_code,)
    ).fetchone()
    today = datetime.now().strftime("%Y%m%d")
    if row and row[0] and row[0] >= today:
        return 0

    try:
        df = pro.stk_holdernumber(ts_code=ts_code,
                                  fields="ts_code,ann_date,end_date,holder_num")
        time.sleep(RATE_LIMIT_SLEEP)
    except Exception as e:
        log.debug("stk_holdernumber() 失败 %s: %s", ts_code, e)
        return 0

    if df is None or df.empty:
        return 0

    rows = [
        (r.ts_code,
         r.ann_date if pd.notna(r.ann_date) else None,
         r.end_date,
         int(r.holder_num) if pd.notna(r.holder_num) else None)
        for r in df.itertuples(index=False)
    ]

    with DB_LOCK:
        conn.executemany("""
            INSERT OR REPLACE INTO stk_holdernumber
              (ts_code, ann_date, end_date, holder_num)
            VALUES (?,?,?,?)
        """, rows)
        conn.execute("""
            INSERT OR REPLACE INTO holder_log
              (ts_code, last_fetched, rows_total, updated_at)
            VALUES (?, ?, COALESCE(
                (SELECT rows_total FROM holder_log WHERE ts_code=?), 0) + ?,
                datetime('now','localtime'))
        """, (ts_code, today, ts_code, len(rows)))
        conn.commit()

    return len(rows)


# ═══════════════════════════════════════════════════════════════════════════════
# v2.3 新增拉取函数
# ═══════════════════════════════════════════════════════════════════════════════
def fetch_daily_basic_one(conn, ts_code, global_start, global_end):
    rng = _get_incremental_range(conn, "daily_basic", ts_code, global_start, global_end)
    if rng is None:
        return 0
    start, end = rng
    try:
        df = pro.daily_basic(ts_code=ts_code, start_date=start, end_date=end,
                             fields="ts_code,trade_date,turnover_rate,volume_ratio,"
                                    "pe,pb,ps,total_share,float_share,free_share,total_mv,circ_mv")
        time.sleep(RATE_LIMIT_SLEEP)
    except Exception as e:
        log.debug("daily_basic 失败 %s: %s", ts_code, e)
        return 0
    if df is None or df.empty:
        return 0
    rows = [(r.ts_code, r.trade_date,
             _safe(r,'turnover_rate'), _safe(r,'volume_ratio'),
             _safe(r,'pe'), _safe(r,'pb'), _safe(r,'ps'),
             _safe(r,'total_share'), _safe(r,'float_share'), _safe(r,'free_share'),
             _safe(r,'total_mv'), _safe(r,'circ_mv')) for r in df.itertuples(index=False)]
    with DB_LOCK:
        conn.executemany("INSERT OR REPLACE INTO daily_basic VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.execute("INSERT OR REPLACE INTO daily_basic_log (ts_code,last_start,last_end,rows_total,updated_at) VALUES (?,?,?,COALESCE((SELECT rows_total FROM daily_basic_log WHERE ts_code=?),0)+?,datetime('now','localtime'))",
                     (ts_code, start, end, ts_code, len(rows)))
        conn.commit()
    return len(rows)


def fetch_margin_one(conn, ts_code, global_start, global_end):
    rng = _get_incremental_range(conn, "margin_detail", ts_code, global_start, global_end)
    if rng is None:
        return 0
    start, end = rng
    try:
        df = pro.margin_detail(ts_code=ts_code, start_date=start, end_date=end,
                               fields="ts_code,trade_date,rzye,rqye,rzmre,rqyl,rzche,rqchl,rqmcl,rzrqye")
        time.sleep(RATE_LIMIT_SLEEP)
    except Exception as e:
        log.debug("margin_detail 失败 %s: %s", ts_code, e)
        return 0
    if df is None or df.empty:
        return 0
    rows = [(r.ts_code, r.trade_date,
             _safe(r,'rzye'), _safe(r,'rqye'), _safe(r,'rzmre'), _safe(r,'rqyl'),
             _safe(r,'rzche'), _safe(r,'rqchl'), _safe(r,'rqmcl'), _safe(r,'rzrqye'))
            for r in df.itertuples(index=False)]
    with DB_LOCK:
        conn.executemany("INSERT OR REPLACE INTO margin_detail VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        conn.execute("INSERT OR REPLACE INTO margin_log (ts_code,last_start,last_end,rows_total,updated_at) VALUES (?,?,?,COALESCE((SELECT rows_total FROM margin_log WHERE ts_code=?),0)+?,datetime('now','localtime'))",
                     (ts_code, start, end, ts_code, len(rows)))
        conn.commit()
    return len(rows)


def fetch_block_trade_one(conn, ts_code, global_start, global_end):
    rng = _get_incremental_range(conn, "block_trade", ts_code, global_start, global_end)
    if rng is None:
        return 0
    start, end = rng
    try:
        df = pro.block_trade(ts_code=ts_code, start_date=start, end_date=end,
                             fields="ts_code,trade_date,price,vol,amount,buyer,seller")
        time.sleep(RATE_LIMIT_SLEEP)
    except Exception as e:
        log.debug("block_trade 失败 %s: %s", ts_code, e)
        return 0
    if df is None or df.empty:
        return 0
    rows = [(r.ts_code, r.trade_date,
             _safe(r,'price'), _safe(r,'vol'), _safe(r,'amount'),
             _safe(r,'buyer'), _safe(r,'seller'), None)
            for r in df.itertuples(index=False)]
    with DB_LOCK:
        conn.executemany("INSERT OR REPLACE INTO block_trade VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.execute("INSERT OR REPLACE INTO block_log (ts_code,last_start,last_end,rows_total,updated_at) VALUES (?,?,?,COALESCE((SELECT rows_total FROM block_log WHERE ts_code=?),0)+?,datetime('now','localtime'))",
                     (ts_code, start, end, ts_code, len(rows)))
        conn.commit()
    return len(rows)


def fetch_bak_basic_one(conn, ts_code, global_start, global_end):
    rng = _get_incremental_range(conn, "bak_basic", ts_code, global_start, global_end)
    if rng is None:
        return 0
    start, end = rng
    try:
        df = pro.bak_basic(ts_code=ts_code, start_date=start, end_date=end,
                           fields="ts_code,trade_date,name,industry,area,pe,float_share,"
                                  "total_share,total_assets,liquid_assets,fixed_assets,"
                                  "holder_num,list_date,undp,per_undp,rev_yoy,profit_yoy,gpr,npr")
        time.sleep(RATE_LIMIT_SLEEP)
    except Exception as e:
        log.debug("bak_basic 失败 %s: %s", ts_code, e)
        return 0
    if df is None or df.empty:
        return 0

    # ✅ 防御：检查必要字段存在，并剔除 trade_date 为空的行
    if 'trade_date' not in df.columns:
        log.warning("bak_basic 缺少 trade_date 字段，跳过 %s", ts_code)
        return 0
    df = df.dropna(subset=['trade_date'])
    if df.empty:
        return 0

    rows = []
    for r in df.itertuples(index=False):
        trade_date = r.trade_date
        if pd.isna(trade_date) or str(trade_date).strip() == '':
            continue
        rows.append((
            r.ts_code, trade_date,
            _safe(r,'name'), _safe(r,'industry'), _safe(r,'area'),
            _safe(r,'pe'), _safe(r,'float_share'), _safe(r,'total_share'),
            _safe(r,'total_assets'), _safe(r,'liquid_assets'), _safe(r,'fixed_assets'),
            _safe(r,'holder_num'), _safe(r,'list_date'), _safe(r,'undp'),
            _safe(r,'per_undp'), _safe(r,'rev_yoy'), _safe(r,'profit_yoy'),
            _safe(r,'gpr'), _safe(r,'npr')
        ))
    if not rows:
        return 0

    with DB_LOCK:
        conn.executemany("INSERT OR REPLACE INTO bak_basic VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.execute("INSERT OR REPLACE INTO bak_basic_log (ts_code,last_start,last_end,rows_total,updated_at) VALUES (?,?,?,COALESCE((SELECT rows_total FROM bak_basic_log WHERE ts_code=?),0)+?,datetime('now','localtime'))",
                     (ts_code, start, end, ts_code, len(rows)))
        conn.commit()
    return len(rows)


def fetch_mins_recent(conn, ts_code, days=5):
    today = datetime.now()
    start_d = (today - timedelta(days=days)).strftime("%Y%m%d")
    end_d   = today.strftime("%Y%m%d")
    row = conn.execute("SELECT last_fetched FROM mins_log WHERE ts_code = ?", (ts_code,)).fetchone()
    if row and row[0] == end_d:
        return 0
    all_data = []
    cur = datetime.strptime(start_d, "%Y%m%d")
    while cur.strftime("%Y%m%d") <= end_d:
        date_str = cur.strftime("%Y%m%d")
        try:
            df = pro.stk_mins(ts_code=ts_code, trade_date=date_str, freq='5min',
                              fields="ts_code,trade_time,open,high,low,close,vol,amount")
            time.sleep(RATE_LIMIT_SLEEP)
            if df is not None and not df.empty:
                all_data.append(df)
        except Exception as e:
            log.debug("stk_mins 失败 %s %s: %s", ts_code, date_str, e)
        cur += timedelta(days=1)
    if not all_data:
        return 0
    df_mins = pd.concat(all_data, ignore_index=True)
    rows = [(r.ts_code, r.trade_time,
             r.open, r.high, r.low, r.close, r.vol, r.amount)
            for r in df_mins.itertuples(index=False)]
    with DB_LOCK:
        conn.execute("DELETE FROM stk_mins WHERE ts_code = ? AND trade_time >= ?", (ts_code, start_d))
        conn.executemany("INSERT OR REPLACE INTO stk_mins VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.execute("INSERT OR REPLACE INTO mins_log (ts_code,last_fetched,rows_total,updated_at) VALUES (?,?,?,datetime('now','localtime'))",
                     (ts_code, end_d, len(rows)))
        conn.commit()
    return len(rows)


# ═══════════════════════════════════════════════════════════════════════════════
# 批量拉取主函数
# ═══════════════════════════════════════════════════════════════════════════════
def batch_fetch(stock_df, global_start, global_end, max_workers=1,
                fetch_money=True, fetch_holder=True,
                fetch_daily_basic=True, fetch_margin=True,
                fetch_block=True, fetch_bak=True, fetch_mins=True):
    total = len(stock_df)
    done = 0; skipped = 0; errors = 0; new_rows = 0
    start_ts = time.time()

    modes = ["日线"]
    if fetch_money: modes.append("资金流向")
    if fetch_holder: modes.append("股东户数")
    if fetch_daily_basic: modes.append("daily_basic")
    if fetch_margin: modes.append("融资融券")
    if fetch_block: modes.append("大宗交易")
    if fetch_bak: modes.append("bak_basic")
    if fetch_mins: modes.append("分钟线(近5天)")

    def _worker(row):
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        try:
            n = fetch_and_save_one(conn, row.ts_code, row.name, global_start, global_end)
            n += fetch_moneyflow_one(conn, row.ts_code, global_start, global_end) if fetch_money else 0
            n += fetch_holder_one(conn, row.ts_code) if fetch_holder else 0
            n += fetch_daily_basic_one(conn, row.ts_code, global_start, global_end) if fetch_daily_basic else 0
            n += fetch_margin_one(conn, row.ts_code, global_start, global_end) if fetch_margin else 0
            n += fetch_block_trade_one(conn, row.ts_code, global_start, global_end) if fetch_block else 0
            n += fetch_bak_basic_one(conn, row.ts_code, global_start, global_end) if fetch_bak else 0
            if fetch_mins:
                n += fetch_mins_recent(conn, row.ts_code, days=MINS_RECENT_DAYS)
            return row.ts_code, n, None
        except Exception as e:
            return row.ts_code, 0, str(e)
        finally:
            conn.close()

    log.info("🚀 批量拉取 [%s]：共 %d 只股票，%s~%s，并发 %d",
             " + ".join(modes), total, global_start, global_end, max_workers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, r): r.ts_code for r in stock_df.itertuples(index=False)}
        for future in as_completed(futures):
            ts_code, n, err = future.result()
            done += 1
            if err:
                errors += 1
                log.error("❌ [%d/%d] %s 失败：%s", done, total, ts_code, err)
            elif n == 0:
                skipped += 1
            else:
                new_rows += n
                log.info("✅ [%d/%d] %-12s +%d 行", done, total, ts_code, n)

            if done % 100 == 0 or done == total:
                elapsed = time.time() - start_ts
                speed = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / speed if speed > 0 else 0
                log.info("📊 进度 %d/%d (%.1f%%) | 新增 %d 行 | 跳过 %d | 错误 %d | 耗时 %.0fs ETA %.0fs",
                         done, total, done/total*100, new_rows, skipped, errors, elapsed, eta)

    elapsed = time.time() - start_ts
    log.info("\n🏁 完成！耗时 %.1f 分 | 处理 %d 只 | 新增 %d 行 | 跳过 %d | 错误 %d",
             elapsed/60, total, new_rows, skipped, errors)


# ═══════════════════════════════════════════════════════════════════════════════
# 命令行入口
# ═══════════════════════════════════════════════════════════════════════════════
def parse_args():
    parser = argparse.ArgumentParser(description="Tushare 全市场数据拉取 v2.3")
    today = datetime.now().strftime("%Y%m%d")
    parser.add_argument("--start", default="20200101", help="起始日期 YYYYMMDD")
    parser.add_argument("--end", default=today, help="截止日期 YYYYMMDD")
    parser.add_argument("--workers", type=int, default=1, help="并发线程数")
    parser.add_argument("--refresh-list", action="store_true", help="强制刷新股票列表缓存")
    parser.add_argument("--code", default=None, help="只拉取指定股票（调试）")
    parser.add_argument("--skip-moneyflow", action="store_true", help="跳过资金流向")
    parser.add_argument("--skip-holder", action="store_true", help="跳过股东户数")
    parser.add_argument("--skip-daily-basic", action="store_true", help="跳过 daily_basic")
    parser.add_argument("--skip-margin", action="store_true", help="跳过融资融券")
    parser.add_argument("--skip-block", action="store_true", help="跳过大宗交易")
    parser.add_argument("--skip-bak", action="store_true", help="跳过 bak_basic")
    parser.add_argument("--skip-mins", action="store_true", help="跳过分钟线拉取")
    return parser.parse_args()


def main():
    args = parse_args()

    log.info("=" * 60)
    log.info("  StockAI · 日线数据批量拉取 v2.3")
    log.info("  日期范围：%s ~ %s", args.start, args.end)
    log.info("  数据库：%s", DB_PATH)
    log.info("  日志：%s", LOG_FILE)
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
        log.info("🔍 调试模式：仅拉取 %s", args.code)

    conn.close()

    batch_fetch(
        stock_df,
        args.start, args.end,
        max_workers      = args.workers,
        fetch_money      = not args.skip_moneyflow,
        fetch_holder     = not args.skip_holder,
        fetch_daily_basic= not args.skip_daily_basic,
        fetch_margin     = not args.skip_margin,
        fetch_block      = not args.skip_block,
        fetch_bak        = not args.skip_bak,
        fetch_mins       = not args.skip_mins,
    )

    log.info("✅ 全部完成！数据库路径：%s", DB_PATH)


if __name__ == "__main__":
    main()
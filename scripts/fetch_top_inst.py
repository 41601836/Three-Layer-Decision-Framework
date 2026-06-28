# -*- coding: utf-8 -*-
"""
fetch_top_inst.py —— 龙虎榜机构净买入数据采集
==============================================

功能说明：
  拉取 Tushare top_inst 接口的龙虎榜机构成交明细，入库后为 ML 回测策略
  的 --require_top_inst 触发器提供真实事件驱动数据支撑。

接口限制：
  - top_inst 接口仅支持按「单交易日」查询（trade_date 为必选参数）
  - 需要 Tushare 积分 >= 2000
  - 单次最大返回 3000 行，龙虎榜每日上榜机构数量远低于此上限，无需分页

数据库表结构（top_inst）：
  ts_code     TEXT  — 股票代码
  trade_date  TEXT  — 交易日期
  exalter     TEXT  — 营业部/机构名称
  buy         REAL  — 买入金额（万元）
  buy_rate    REAL  — 买入占总成交比例
  sell        REAL  — 卖出金额（万元）
  sell_rate   REAL  — 卖出占总成交比例
  net_buy     REAL  — 净成交额（万元），正数=净买入，负数=净卖出
  reason      TEXT  — 上榜理由

使用方法：
    # 拉取 2025 全年数据
    python scripts/fetch_top_inst.py --start 20250101 --end 20251231

    # 仅拉取今年至今
    python scripts/fetch_top_inst.py

    # 检查当前数据库中已有数据
    python scripts/fetch_top_inst.py --check

    # 强制重新拉取（会覆盖已有数据）
    python scripts/fetch_top_inst.py --start 20250101 --end 20251231 --force
"""

import os
import sys
import sqlite3
import argparse
import time
import logging
from datetime import datetime, timedelta

import pandas as pd
import tushare as ts

# ─── 路径配置 ─────────────────────────────────────────────────────────────────
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")
LOG_DIR  = os.path.join(ROOT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# ─── 日志配置 ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(
            os.path.join(LOG_DIR, f"fetch_top_inst_{datetime.now():%Y%m%d_%H%M%S}.log"),
            encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ─── Tushare Token ────────────────────────────────────────────────────────────
try:
    sys.path.insert(0, ROOT_DIR)
    from scripts.tokens import TOKEN as TUSHARE_TOKEN
except ImportError:
    try:
        from tokens import TOKEN as TUSHARE_TOKEN
    except ImportError:
        TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

if not TUSHARE_TOKEN:
    log.error("未找到 Tushare Token，请检查 scripts/tokens.py 或环境变量 TUSHARE_TOKEN")
    sys.exit(1)

# ─── 常量 ─────────────────────────────────────────────────────────────────────
# top_inst 接口限流：每日调1次，不超过 200次/分钟
SLEEP_BETWEEN_DAYS = 0.4   # 每日请求间隔（秒），保守设置避免频率限制
MAX_RETRY = 3              # 单日失败最大重试次数
RETRY_DELAY = 5.0          # 重试等待（秒）


# =============================================================================
# 数据库初始化
# =============================================================================
def init_db(conn):
    """初始化 top_inst 表及索引（幂等操作）"""
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.executescript("""
        -- 龙虎榜机构净买入明细
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
        CREATE INDEX IF NOT EXISTS idx_top_inst_date
            ON top_inst (trade_date);
        CREATE INDEX IF NOT EXISTS idx_top_inst_code
            ON top_inst (ts_code, trade_date);

        -- 拉取进度日志（按日期记录，支持断点续拉）
        CREATE TABLE IF NOT EXISTS top_inst_log (
            trade_date TEXT PRIMARY KEY,
            rows_saved INTEGER DEFAULT 0,
            fetched_at TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    conn.commit()
    log.info("top_inst 表初始化完成")


# =============================================================================
# 获取交易日历
# =============================================================================
def get_trade_dates(pro, start_date, end_date):
    """
    通过 trade_cal 接口获取指定区间的交易日列表（仅 is_open=1 的日期）。
    """
    log.info("拉取交易日历: %s ~ %s ...", start_date, end_date)
    try:
        cal_df = pro.trade_cal(
            exchange="SSE",
            start_date=start_date,
            end_date=end_date,
            fields="cal_date,is_open"
        )
        trade_days = cal_df[cal_df["is_open"] == 1]["cal_date"].sort_values().tolist()
        log.info("共 %d 个交易日", len(trade_days))
        return trade_days
    except Exception as e:
        log.error("获取交易日历失败: %s", e)
        # 降级：按自然日遍历，跳过周末
        log.warning("降级为自然日遍历（跳过周末），注意节假日数据可能为空")
        dates = []
        cur = datetime.strptime(start_date, "%Y%m%d")
        end = datetime.strptime(end_date,   "%Y%m%d")
        while cur <= end:
            if cur.weekday() < 5:  # 0=周一 ... 4=周五
                dates.append(cur.strftime("%Y%m%d"))
            cur += timedelta(days=1)
        return dates


# =============================================================================
# 单日数据拉取
# =============================================================================
def fetch_one_day(pro, trade_date):
    """
    拉取单个交易日的龙虎榜机构成交明细。
    非交易日或无上榜时返回空 DataFrame。
    """
    for attempt in range(1, MAX_RETRY + 1):
        try:
            df = pro.top_inst(trade_date=trade_date)
            if df is None:
                return pd.DataFrame()
            # 标准化列名（Tushare 不同版本可能有差异）
            df.columns = [c.lower().strip() for c in df.columns]
            # 确保 trade_date 字段存在（有时接口不返回）
            if "trade_date" not in df.columns:
                df["trade_date"] = trade_date
            return df
        except Exception as e:
            err_msg = str(e)
            if "积分不足" in err_msg or "权限" in err_msg or "2000" in err_msg:
                log.error("Tushare 积分不足（需要 >= 2000 积分），请升级账户权限")
                raise PermissionError("Tushare top_inst 权限不足") from e
            if attempt < MAX_RETRY:
                log.warning("%s 第 %d 次尝试失败，%.1fs 后重试: %s",
                            trade_date, attempt, RETRY_DELAY, e)
                time.sleep(RETRY_DELAY)
            else:
                log.warning("%s 全部 %d 次尝试均失败，跳过: %s",
                            trade_date, MAX_RETRY, e)
                return pd.DataFrame()


# =============================================================================
# 已获取日期查询（支持断点续拉）
# =============================================================================
def get_already_fetched_dates(conn):
    """返回数据库中已成功拉取的交易日集合"""
    try:
        rows = conn.execute("SELECT trade_date FROM top_inst_log").fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


# =============================================================================
# 数据入库
# =============================================================================
def save_day(conn, trade_date, df, force=False):
    """
    将单日数据写入数据库（INSERT OR REPLACE 去重模式）。
    """
    if force:
        conn.execute("DELETE FROM top_inst WHERE trade_date = ?", (trade_date,))
        conn.execute("DELETE FROM top_inst_log WHERE trade_date = ?", (trade_date,))

    rows_saved = 0
    if not df.empty:
        # 仅保留已知字段，兼容 Tushare 接口字段变动
        keep_cols = [c for c in ["ts_code", "trade_date", "exalter",
                                 "buy", "buy_rate", "sell", "sell_rate",
                                 "net_buy", "reason"] if c in df.columns]
        df_save = df[keep_cols].copy()

        # 数值字段强制转换，防止字符串入库
        for col in ["buy", "buy_rate", "sell", "sell_rate", "net_buy"]:
            if col in df_save.columns:
                df_save[col] = pd.to_numeric(df_save[col], errors="coerce")

        df_save.to_sql("top_inst", conn, if_exists="append", index=False,
                       method="multi", chunksize=500)
        rows_saved = len(df_save)

    # 无论有无数据，都记录本日已拉取（避免下次重复请求）
    conn.execute("""
        INSERT OR REPLACE INTO top_inst_log (trade_date, rows_saved, fetched_at)
        VALUES (?, ?, datetime('now','localtime'))
    """, (trade_date, rows_saved))
    conn.commit()
    return rows_saved


# =============================================================================
# 数据统计与验证
# =============================================================================
def check_data(conn, year="2025"):
    """统计数据库中指定年份的 top_inst 数据覆盖情况。"""
    try:
        total_rows = conn.execute(
            "SELECT COUNT(*) FROM top_inst WHERE trade_date LIKE ?",
            (f"{year}%",)
        ).fetchone()[0]

        total_days = conn.execute(
            "SELECT COUNT(DISTINCT trade_date) FROM top_inst WHERE trade_date LIKE ?",
            (f"{year}%",)
        ).fetchone()[0]

        net_buy_rows = conn.execute(
            "SELECT COUNT(*) FROM top_inst WHERE trade_date LIKE ? AND net_buy > 0",
            (f"{year}%",)
        ).fetchone()[0]

        # 按月统计
        monthly = conn.execute("""
            SELECT substr(trade_date, 1, 6) as month,
                   COUNT(*) as rows,
                   COUNT(DISTINCT ts_code) as stocks,
                   SUM(CASE WHEN net_buy > 0 THEN 1 ELSE 0 END) as net_buy_cnt
            FROM top_inst
            WHERE trade_date LIKE ?
            GROUP BY month
            ORDER BY month
        """, (f"{year}%",)).fetchall()

        log.info("=" * 55)
        log.info("top_inst 数据统计（%s年）", year)
        log.info("=" * 55)
        log.info("  总记录数       : %d 条", total_rows)
        log.info("  覆盖交易日数   : %d 天", total_days)
        log.info("  净买入记录数   : %d 条（net_buy > 0）", net_buy_rows)
        log.info("─" * 55)
        log.info("  月份      行数    涉及股票  净买入条目")
        for row in monthly:
            log.info("  %s    %4d    %4d      %4d", row[0], row[1], row[2], row[3])
        log.info("=" * 55)

        return total_rows, total_days, net_buy_rows
    except Exception as e:
        log.error("统计失败: %s", e)
        return 0, 0, 0


# =============================================================================
# 主函数
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="龙虎榜机构净买入数据采集（top_inst）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python scripts/fetch_top_inst.py
  python scripts/fetch_top_inst.py --start 20250101 --end 20251231
  python scripts/fetch_top_inst.py --check
  python scripts/fetch_top_inst.py --start 20250101 --force
        """
    )
    parser.add_argument("--start",  default="20250101",
                        help="开始日期（YYYYMMDD，默认2025年初）")
    parser.add_argument("--end",    default=datetime.now().strftime("%Y%m%d"),
                        help="结束日期（YYYYMMDD，默认今天）")
    parser.add_argument("--check",  action="store_true",
                        help="仅检查当前数据量，不拉取新数据")
    parser.add_argument("--force",  action="store_true",
                        help="强制重新拉取（覆盖已有数据，忽略断点记录）")
    parser.add_argument("--year",   default="2025",
                        help="--check 时统计的年份（默认2025）")
    args = parser.parse_args()

    # ── 连接数据库 ────────────────────────────────────────────────────────────
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    init_db(conn)

    # ── 仅检查模式 ────────────────────────────────────────────────────────────
    if args.check:
        total, days, net = check_data(conn, args.year)
        if total == 0:
            log.warning("top_inst 表中没有任何数据，请先运行拉取命令")
        elif days < 50:
            log.warning("覆盖交易日 %d 天，数据偏少，建议补拉", days)
        else:
            log.info("数据覆盖充足，--require_top_inst 触发器可用")
        conn.close()
        return

    # ── Tushare 初始化 ────────────────────────────────────────────────────────
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()
    log.info("Tushare 接口初始化完成")

    # ── 获取交易日历 ──────────────────────────────────────────────────────────
    trade_dates = get_trade_dates(pro, args.start, args.end)
    if not trade_dates:
        log.error("未找到有效交易日，请检查日期范围")
        conn.close()
        return

    # ── 断点续拉：跳过已拉取日期 ─────────────────────────────────────────────
    already_done = set() if args.force else get_already_fetched_dates(conn)
    pending_dates = [d for d in trade_dates if d not in already_done]

    if not pending_dates:
        log.info("所有 %d 个交易日均已拉取，无需重复操作（使用 --force 强制覆盖）",
                 len(trade_dates))
        check_data(conn, args.year)
        conn.close()
        return

    log.info("待拉取: %d 天（已完成: %d 天，共: %d 天）",
             len(pending_dates), len(already_done), len(trade_dates))

    # ── 逐日拉取 ─────────────────────────────────────────────────────────────
    total_rows    = 0
    success_days  = 0
    empty_days    = 0
    failed_days   = 0

    start_time = datetime.now()
    log.info("开始拉取 top_inst 龙虎榜机构数据...")
    log.info("─" * 55)

    for i, trade_date in enumerate(pending_dates, 1):
        try:
            df = fetch_one_day(pro, trade_date)
            rows = save_day(conn, trade_date, df, force=args.force)

            if df.empty:
                empty_days += 1
                log.debug("[%d/%d] %s 无上榜数据（非交易日或无龙虎榜）",
                          i, len(pending_dates), trade_date)
            else:
                success_days += 1
                total_rows += rows
                net_buy_cnt = int((df["net_buy"] > 0).sum()) if "net_buy" in df.columns else 0
                log.info("[%d/%d] %s %d条（净买入:%d）",
                         i, len(pending_dates), trade_date, rows, net_buy_cnt)

        except PermissionError:
            # 权限不足，立即终止
            log.error("因权限不足，终止拉取。已完成 %d 天。", i - 1)
            break
        except Exception as e:
            failed_days += 1
            log.error("[%d/%d] %s 发生未知错误: %s", i, len(pending_dates), trade_date, e)

        time.sleep(SLEEP_BETWEEN_DAYS)

        # 每 50 天输出一次进度摘要
        if i % 50 == 0:
            elapsed = (datetime.now() - start_time).seconds
            log.info("── 进度: %d/%d | 已入库: %d条 | 耗时: %ds ──",
                     i, len(pending_dates), total_rows, elapsed)

    # ── 汇总报告 ─────────────────────────────────────────────────────────────
    elapsed = (datetime.now() - start_time).seconds
    log.info("=" * 55)
    log.info("拉取完成！耗时 %ds", elapsed)
    log.info("  成功天数（有数据）: %d", success_days)
    log.info("  空数据天数        : %d（非交易日或无上榜）", empty_days)
    log.info("  失败天数          : %d", failed_days)
    log.info("  新增记录总数      : %d 条", total_rows)
    log.info("=" * 55)

    # 最终统计
    check_data(conn, args.year)

    conn.close()
    log.info("top_inst 数据入库完成，数据库已关闭")
    log.info("提示：运行 python backtest_ml_strategy.py --require_top_inst 验证效果")


if __name__ == "__main__":
    main()

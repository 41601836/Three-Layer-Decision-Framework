# -*- coding: utf-8 -*-
"""
dao.py —— SQLite 数据库交互统一 DAO 层
"""
import os
import sqlite3
import pandas as pd
from utils.logger import scheduler_log

# 确定 stock_daily.db 的绝对路径，保证不论从哪个工作目录下启动脚本均可正确连接
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_daily.db")

def get_conn() -> sqlite3.Connection:
    """获取 SQLite 数据库连接"""
    return sqlite3.connect(DB_PATH, timeout=30)

def delete_by_date(table_name: str, trade_date: str):
    """
    根据交易日清理对应表的旧数据，实现幂等写入
    """
    conn = get_conn()
    try:
        with conn:
            conn.execute(f"DELETE FROM {table_name} WHERE trade_date = ?", (trade_date,))
        scheduler_log.info(f"ℹ️ [DAO] 成功清理表 [{table_name}] 在日期 [{trade_date}] 的旧数据")
    except sqlite3.OperationalError as oe:
        if "no such table" in str(oe) or "no such column" in str(oe):
            scheduler_log.info(f"ℹ️ [DAO] 表 [{table_name}] 尚不存在或无对应日期列，跳过日期清理逻辑。")
        else:
            raise
    except Exception as e:
        scheduler_log.error(f"❌ [DAO] 清理表 [{table_name}] 的日期数据时发生异常: {e}")
        raise
    finally:
        conn.close()

def delete_by_snapshot_time(table_name: str, snapshot_time: str):
    """
    根据快照时间清理对应表的旧数据
    """
    conn = get_conn()
    try:
        with conn:
            conn.execute(f"DELETE FROM {table_name} WHERE snapshot_time = ?", (snapshot_time,))
        scheduler_log.info(f"ℹ️ [DAO] 成功清理表 [{table_name}] 在时间 [{snapshot_time}] 的旧数据")
    except sqlite3.OperationalError as oe:
        if "no such table" in str(oe) or "no such column" in str(oe):
            scheduler_log.info(f"ℹ️ [DAO] 表 [{table_name}] 尚不存在或无对应快照时间列，跳过快照时间清理逻辑。")
        else:
            raise
    except Exception as e:
        scheduler_log.error(f"❌ [DAO] 清理表 [{table_name}] 的快照数据时发生异常: {e}")
        raise
    finally:
        conn.close()

def delete_by_month(table_name: str, stat_month: str):
    """
    根据统计月份清理对应表的旧数据
    """
    conn = get_conn()
    try:
        with conn:
            conn.execute(f"DELETE FROM {table_name} WHERE stat_month = ?", (stat_month,))
        scheduler_log.info(f"ℹ️ [DAO] 成功清理表 [{table_name}] 在月份 [{stat_month}] 的旧数据")
    except sqlite3.OperationalError as oe:
        if "no such table" in str(oe) or "no such column" in str(oe):
            scheduler_log.info(f"ℹ️ [DAO] 表 [{table_name}] 尚不存在或无对应月份列，跳过月份清理逻辑。")
        else:
            raise
    except Exception as e:
        scheduler_log.error(f"❌ [DAO] 清理表 [{table_name}] 的月份数据时发生异常: {e}")
        raise
    finally:
        conn.close()

def batch_insert(table_name: str, records: list):
    """
    批量插入数据，启用事务机制。
    针对 stock_list 和 trade_cal 等全量基础信息表，使用 replace 覆盖写入；
    针对增量日线/快照表，使用 append 追加写入。若表不存在，会自动创建表。
    """
    if not records:
        scheduler_log.warning(f"⚠️ [DAO] 待插入表 [{table_name}] 的数据列表为空，跳过入库。")
        return

    conn = get_conn()
    try:
        df = pd.DataFrame(records)
        if_exists = "replace" if table_name in ["stock_list", "trade_cal"] else "append"
        with conn:
            df.to_sql(table_name, conn, if_exists=if_exists, index=False)
        scheduler_log.info(f"✅ [DAO] 批量写入表 [{table_name}] 成功 (模式: {if_exists})，录入条数: {len(records)}")
    except Exception as e:
        scheduler_log.error(f"❌ [DAO] 批量写入表 [{table_name}] 事务失败，数据已自动回滚。错误原因: {e}")
        raise
    finally:
        conn.close()

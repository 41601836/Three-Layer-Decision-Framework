import logging
from typing import List, Dict, Any
from app.dao.base_dao import execute_query, execute_update

logger = logging.getLogger(__name__)

def aggregate_sectors(trade_date: str):
    """
    聚合指定日期的板块数据
    """
    logger.info(f"Start aggregating sectors for trade_date: {trade_date}")
    
    # 获取最新的概念映射日期
    latest_mapping_date_res = execute_query("SELECT MAX(trade_date) as max_date FROM concept_mapping WHERE trade_date <= ?", (trade_date,))
    mapping_date = latest_mapping_date_res[0]['max_date'] if latest_mapping_date_res and latest_mapping_date_res[0]['max_date'] else trade_date

    # 我们需要通过 SQL 联合查询 daily_prices, moneyflow (代替 moneyflow_daily), limit_data (这里原先的系统里可能叫 limit_list_d，如果是 limit_data 则保持)
    # 本项目的涨跌停表名叫 limit_list_d 还是 daily_prices?
    # 为了兼容之前的数据库结构，我们使用 pct_chg > 9.5 算作涨停。
    # 按照 Task 要求，结合 daily_prices, moneyflow, 和 concept_mapping。
    
    sql = """
        SELECT 
            c.concept_name as sector_name,
            AVG(d.pct_chg) as pct_chg,
            SUM(d.amount) / 100000000.0 as amount, -- 转为亿元 (mootdx 单位是元)
            SUM(COALESCE(m.net_mf_amount, 0)) / 10000.0 as net_mf_amount, -- 转为亿元
            SUM(CASE WHEN d.pct_chg >= 9.5 THEN 1 ELSE 0 END) as limit_up_count,
            COUNT(DISTINCT c.ts_code) as total_stocks
        FROM concept_mapping c
        JOIN daily_prices d ON c.ts_code = d.ts_code AND d.trade_date = ?
        LEFT JOIN moneyflow m ON d.ts_code = m.ts_code AND d.trade_date = ?
        WHERE c.trade_date = ?
        GROUP BY c.concept_name
    """
    
    rows = execute_query(sql, (trade_date, trade_date, mapping_date))
    
    if not rows:
        logger.warning(f"No aggregated data found for {trade_date}")
        return
        
    # 清空该日旧数据
    execute_update("DELETE FROM sector_daily_stats WHERE trade_date = ?", (trade_date,))
    
    # 批量插入新数据
    insert_sql = """
        INSERT INTO sector_daily_stats 
        (trade_date, sector_name, pct_chg, amount, net_mf_amount, limit_up_count, total_stocks, coverage_rate)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    
    inserted = 0
    for row in rows:
        coverage_rate = 0
        if row['total_stocks'] > 0:
            coverage_rate = (row['limit_up_count'] / row['total_stocks']) * 100.0
            
        execute_update(insert_sql, (
            trade_date,
            row['sector_name'],
            row['pct_chg'],
            row['amount'],
            row['net_mf_amount'],
            row['limit_up_count'],
            row['total_stocks'],
            coverage_rate
        ))
        inserted += 1
        
    logger.info(f"Aggregate completed. {inserted} sectors inserted for {trade_date}.")


def get_sector_stats(trade_date: str) -> List[Dict[str, Any]]:
    """读取热力图聚合结果供 API 调用"""
    sql = "SELECT * FROM sector_daily_stats WHERE trade_date = ?"
    return execute_query(sql, (trade_date,))

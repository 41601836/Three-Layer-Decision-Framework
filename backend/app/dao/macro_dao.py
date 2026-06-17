from typing import Optional
from app.dao.base_dao import execute_query, execute_update

def get_macro(indicator_name: str, data_date: str) -> Optional[str]:
    """查询指定日期特定指标的值"""
    sql = """
        SELECT indicator_value 
        FROM macro_cache 
        WHERE indicator_name = ? AND data_date <= ?
        ORDER BY data_date DESC 
        LIMIT 1
    """
    rows = execute_query(sql, (indicator_name, data_date))
    if rows:
        return rows[0]['indicator_value']
    return None

def upsert_macro(macro_type: str, indicator_name: str, value: str, data_date: str, source: str = 'akshare') -> int:
    """插入或更新宏观数据"""
    sql = """
        INSERT INTO macro_cache (macro_type, indicator_name, indicator_value, data_date, source, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
        ON CONFLICT(macro_type, indicator_name, data_date) DO UPDATE SET
            indicator_value = excluded.indicator_value,
            source = excluded.source,
            updated_at = datetime('now', 'localtime');
    """
    return execute_update(sql, (macro_type, indicator_name, str(value), data_date, source))

def get_latest_macro_date(indicator_name: str) -> Optional[str]:
    """获取该指标最新数据的日期"""
    sql = """
        SELECT data_date 
        FROM macro_cache 
        WHERE indicator_name = ?
        ORDER BY data_date DESC 
        LIMIT 1
    """
    rows = execute_query(sql, (indicator_name,))
    if rows:
        return rows[0]['data_date']
    return None

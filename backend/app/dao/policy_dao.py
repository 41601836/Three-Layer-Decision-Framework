from typing import Optional, Dict, Any
from datetime import datetime
from app.dao.base_dao import execute_query, execute_update

def get_policy_status(policy_type: str = 'monetary') -> Optional[Dict[str, Any]]:
    """获取最新政策状态"""
    sql = """
        SELECT policy_status as status, effective_date, notes
        FROM policy_config 
        WHERE policy_type = ?
        ORDER BY effective_date DESC, updated_at DESC
        LIMIT 1
    """
    rows = execute_query(sql, (policy_type,))
    if rows:
        return rows[0]
    return None

def set_policy_status(policy_type: str, status: str, notes: str = '', effective_date: str = None) -> int:
    """更新或插入政策状态"""
    if not effective_date:
        effective_date = datetime.now().strftime('%Y-%m-%d')
        
    sql = """
        INSERT INTO policy_config (policy_type, policy_status, effective_date, notes, updated_at)
        VALUES (?, ?, ?, ?, datetime('now', 'localtime'))
    """
    return execute_update(sql, (policy_type, status, effective_date, notes))

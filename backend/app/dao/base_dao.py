import sqlite3
from typing import List, Dict, Any, Optional, Union
from app.core.database import get_db_conn

def execute_query(sql: str, params: tuple = None) -> List[Dict[str, Any]]:
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def execute_update(sql: str, params: tuple = None) -> int:
    conn = get_db_conn()
    try:
        cursor = conn.cursor()
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        conn.commit()
        # Return lastrowid if it's an INSERT and generated an ID, otherwise rowcount
        return cursor.lastrowid if cursor.lastrowid else cursor.rowcount
    except sqlite3.Error as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

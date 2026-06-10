#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
推送历史记录管理模块
"""
import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Optional, Any
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

class PushHistoryManager:
    """推送历史记录管理器"""
    
    def __init__(self, db_path: str = "db/stock_daily.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 创建推送历史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS push_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                push_time TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                stock_name TEXT NOT NULL,
                push_reason TEXT NOT NULL,
                push_status TEXT NOT NULL DEFAULT 'success',
                push_content TEXT,
                session_name TEXT,
                total_score REAL,
                grade TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        
        # 创建索引以提高查询性能
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_push_time ON push_history(push_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ts_code ON push_history(ts_code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stock_name ON push_history(stock_name)")
        
        conn.commit()
        conn.close()
        log.info("推送历史数据库初始化完成")
    
    def add_push_record(
        self,
        ts_code: str,
        stock_name: str,
        push_reason: str,
        push_content: Any = None,
        session_name: str = None,
        total_score: float = None,
        grade: str = None,
        push_status: str = 'success'
    ) -> int:
        """添加推送记录"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            push_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # 序列化推送内容
            if push_content and isinstance(push_content, (dict, list)):
                push_content_str = json.dumps(push_content, ensure_ascii=False, indent=2)
            else:
                push_content_str = str(push_content) if push_content is not None else None
            
            cursor.execute("""
                INSERT INTO push_history 
                (push_time, ts_code, stock_name, push_reason, push_content, 
                 session_name, total_score, grade, push_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                push_time, ts_code, stock_name, push_reason, push_content_str,
                session_name, total_score, grade, push_status
            ))
            
            record_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            log.debug(f"推送记录已保存: {ts_code} - {stock_name}")
            return record_id
            
        except Exception as e:
            log.error(f"保存推送记录失败: {e}")
            return -1
    
    def get_push_history(
        self,
        page: int = 1,
        page_size: int = 20,
        search: str = None,
        status: str = None,
        grade: str = None,
        start_time: str = None,
        end_time: str = None
    ) -> Dict[str, Any]:
        """获取推送历史列表"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            query = "SELECT * FROM push_history WHERE 1=1"
            params = []
            
            # 添加筛选条件
            if search:
                query += " AND (ts_code LIKE ? OR stock_name LIKE ?)"
                params.extend([f"%{search}%", f"%{search}%"])
            
            if status:
                query += " AND push_status = ?"
                params.append(status)
            
            if grade:
                query += " AND grade = ?"
                params.append(grade)
            
            if start_time:
                query += " AND push_time >= ?"
                params.append(start_time)
            
            if end_time:
                query += " AND push_time <= ?"
                params.append(end_time)
            
            # 分页
            offset = (page - 1) * page_size
            query += " ORDER BY push_time DESC LIMIT ? OFFSET ?"
            params.extend([page_size, offset])
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            # 获取总数
            count_query = "SELECT COUNT(*) FROM push_history WHERE 1=1"
            count_params = params[:-2]  # 移除分页参数
            cursor.execute(count_query, count_params)
            total_count = cursor.fetchone()[0]
            
            conn.close()
            
            # 转换为字典格式
            columns = [desc[0] for desc in cursor.description]
            result_list = []
            
            for row in rows:
                item = dict(zip(columns, row))
                # 反序列化推送内容
                if item.get('push_content'):
                    try:
                        item['push_content'] = json.loads(item['push_content'])
                    except json.JSONDecodeError:
                        pass  # 如果不是JSON格式，保持原字符串
                result_list.append(item)
            
            return {
                'list': result_list,
                'total': total_count,
                'page': page,
                'page_size': page_size,
                'total_pages': (total_count + page_size - 1) // page_size
            }
            
        except Exception as e:
            log.error(f"获取推送历史失败: {e}")
            return {
                'list': [],
                'total': 0,
                'page': page,
                'page_size': page_size,
                'total_pages': 0
            }
    
    def get_push_detail(self, record_id: int) -> Optional[Dict[str, Any]]:
        """获取推送详情"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM push_history WHERE id = ?", (record_id,))
            row = cursor.fetchone()
            
            conn.close()
            
            if row:
                columns = [desc[0] for desc in cursor.description]
                item = dict(zip(columns, row))
                # 反序列化推送内容
                if item.get('push_content'):
                    try:
                        item['push_content'] = json.loads(item['push_content'])
                    except json.JSONDecodeError:
                        pass
                return item
            
            return None
            
        except Exception as e:
            log.error(f"获取推送详情失败: {e}")
            return None
    
    def delete_push_record(self, record_id: int) -> bool:
        """删除推送记录"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("DELETE FROM push_history WHERE id = ?", (record_id,))
            affected_rows = cursor.rowcount
            
            conn.commit()
            conn.close()
            
            if affected_rows > 0:
                log.info(f"推送记录已删除: {record_id}")
                return True
            else:
                log.warning(f"未找到推送记录: {record_id}")
                return False
                
        except Exception as e:
            log.error(f"删除推送记录失败: {e}")
            return False
    
    def clear_all_records(self, days: int = None) -> int:
        """清除所有推送记录（可按天数保留）"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            if days:
                # 计算保留的起始日期
                cutoff_date = (datetime.now() - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
                cursor.execute("DELETE FROM push_history WHERE push_time < ?", (cutoff_date,))
            else:
                cursor.execute("DELETE FROM push_history")
            
            affected_rows = cursor.rowcount
            conn.commit()
            conn.close()
            
            log.info(f"已清除 {affected_rows} 条推送记录")
            return affected_rows
            
        except Exception as e:
            log.error(f"清除推送记录失败: {e}")
            return 0
    
    def get_statistics(self, days: int = 7) -> Dict[str, Any]:
        """获取推送统计信息"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 计算统计起始日期
            start_date = (datetime.now() - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
            
            # 总推送次数
            cursor.execute("SELECT COUNT(*) FROM push_history WHERE push_time >= ?", (start_date,))
            total_pushes = cursor.fetchone()[0]
            
            # 成功推送次数
            cursor.execute("""
                SELECT COUNT(*) FROM push_history 
                WHERE push_time >= ? AND push_status = 'success'
            """, (start_date,))
            success_pushes = cursor.fetchone()[0]
            
            # 按评级统计
            cursor.execute("""
                SELECT grade, COUNT(*) as count 
                FROM push_history 
                WHERE push_time >= ? AND grade IS NOT NULL
                GROUP BY grade
                ORDER BY count DESC
            """, (start_date,))
            grade_stats = cursor.fetchall()
            
            # 按会话统计
            cursor.execute("""
                SELECT session_name, COUNT(*) as count 
                FROM push_history 
                WHERE push_time >= ? AND session_name IS NOT NULL
                GROUP BY session_name
                ORDER BY count DESC
            """, (start_date,))
            session_stats = cursor.fetchall()
            
            # 每日推送统计
            cursor.execute("""
                SELECT DATE(push_time) as date, COUNT(*) as count 
                FROM push_history 
                WHERE push_time >= ?
                GROUP BY DATE(push_time)
                ORDER BY date DESC
            """, (start_date,))
            daily_stats = cursor.fetchall()
            
            conn.close()
            
            # 计算成功率
            success_rate = (success_pushes / total_pushes * 100) if total_pushes > 0 else 0
            
            return {
                'time_range': f"最近 {days} 天",
                'start_date': start_date,
                'total_pushes': total_pushes,
                'success_pushes': success_pushes,
                'success_rate': round(success_rate, 2),
                'grade_stats': dict(grade_stats),
                'session_stats': dict(session_stats),
                'daily_stats': dict(daily_stats)
            }
            
        except Exception as e:
            log.error(f"获取推送统计失败: {e}")
            return {}

# 全局实例
push_history_manager = PushHistoryManager()


# 测试代码
if __name__ == "__main__":
    # 创建测试实例
    manager = PushHistoryManager(":memory:")
    
    # 测试添加记录
    print("测试添加推送记录...")
    record_id = manager.add_push_record(
        ts_code="600519.SH",
        stock_name="贵州茅台",
        push_reason="量化评分88分，AI看好",
        push_content={"title": "测试推送", "content": "这是测试内容"},
        session_name="盘前分析",
        total_score=88.5,
        grade="S",
        push_status="success"
    )
    print(f"添加记录ID: {record_id}")
    
    # 测试获取列表
    print("\n测试获取推送历史...")
    result = manager.get_push_history(page=1, page_size=10)
    print(f"总数: {result['total']}")
    print(f"列表: {result['list']}")
    
    # 测试获取详情
    print("\n测试获取推送详情...")
    detail = manager.get_push_detail(record_id)
    print(f"详情: {detail}")
    
    # 测试统计
    print("\n测试获取统计信息...")
    stats = manager.get_statistics(days=7)
    print(f"统计: {stats}")
    
    print("\n所有测试完成！")

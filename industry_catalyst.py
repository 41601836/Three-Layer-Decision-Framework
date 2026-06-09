# -*- coding: utf-8 -*-
"""
industry_catalyst.py —— 行业催化评分模块
=========================================

将行业强度排名自动映射到个股的催化得分（0-30分）。

核心逻辑：
  - 主线行业（前5名）→ 20-30分
  - 备选行业（6-10名）→ 10-20分
  - 其他行业 → 0-10分

使用方法：
    from industry_catalyst import get_catalyst_score
    score = get_catalyst_score("半导体", conn)
"""

import os
import sqlite3
import pandas as pd

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")


def get_catalyst_score(industry: str, conn=None) -> int:
    """
    根据行业强度排名计算催化得分（0-30）。
    
    参数：
        industry: 行业名称
        conn: SQLite 连接（可选，不传则自动创建）
    
    返回：
        催化得分（0-30）
    """
    _own_conn = conn is None
    if _own_conn:
        conn = sqlite3.connect(DB_PATH)
    
    try:
        df = pd.read_sql("""
            SELECT industry, composite_score, tier
            FROM industry_rank
            ORDER BY composite_score DESC
        """, conn)
        
        if df.empty:
            return 0
        
        total = len(df)
        
        matched = df[df['industry'] == industry]
        if matched.empty:
            return 5
        
        idx = df[df['industry'] == industry].index[0]
        rank = idx + 1
        
        tier = matched.iloc[0]['tier']
        
        if tier == 'main':
            base_score = 25
            adjustment = (5 - rank) * 1
            score = base_score + adjustment
        elif tier == 'backup':
            base_score = 15
            adjustment = (10 - rank) * 1
            score = base_score + adjustment
        else:
            rank_pct = rank / total
            score = int(10 * (1 - rank_pct))
        
        return max(0, min(30, score))
    
    finally:
        if _own_conn:
            conn.close()


def get_stock_industry(ts_code: str, conn=None) -> str:
    """
    获取股票所属行业。
    
    参数：
        ts_code: 股票代码
        conn: SQLite 连接（可选）
    
    返回：
        行业名称，若未找到返回空字符串
    """
    _own_conn = conn is None
    if _own_conn:
        conn = sqlite3.connect(DB_PATH)
    
    try:
        row = conn.execute(
            "SELECT industry FROM stock_list WHERE ts_code = ?",
            (ts_code,)
        ).fetchone()
        
        return row[0] if row else ""
    
    finally:
        if _own_conn:
            conn.close()


def test_catalyst_distribution():
    """
    测试催化得分分布，打印各行业的催化得分。
    """
    conn = sqlite3.connect(DB_PATH)
    
    try:
        df = pd.read_sql("""
            SELECT industry, composite_score, tier
            FROM industry_rank
            ORDER BY composite_score DESC
        """, conn)
        
        if df.empty:
            print("❌ 未找到行业排名数据，请先运行 industry_strength.py")
            return
        
        print("=" * 60)
        print("         行业催化得分分布测试")
        print("=" * 60)
        print(f"{'Rank':<4} {'Tier':<8} {'Industry':<15} {'Score':<6}")
        print("-" * 60)
        
        main_count = 0
        backup_count = 0
        avoid_count = 0
        
        for idx, row in df.iterrows():
            rank = idx + 1
            industry = row['industry']
            tier = row['tier']
            score = get_catalyst_score(industry, conn)
            
            tier_zh = {
                'main': 'MAIN',
                'backup': 'BACKUP',
                'avoid': 'AVOID'
            }.get(tier, 'UNKNOWN')
            
            print(f"{rank:<4} {tier_zh:<8} {industry:<15} {score:<6}")
            
            if tier == 'main':
                main_count += 1
            elif tier == 'backup':
                backup_count += 1
            else:
                avoid_count += 1
        
        print("-" * 60)
        print(f"总计: {len(df)} 个行业")
        print(f"主线行业: {main_count} 个（预期得分 20-30）")
        print(f"备选行业: {backup_count} 个（预期得分 10-20）")
        print(f"回避行业: {avoid_count} 个（预期得分 0-10）")
        
    finally:
        conn.close()


if __name__ == "__main__":
    test_catalyst_distribution()
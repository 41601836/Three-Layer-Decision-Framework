# -*- coding: utf-8 -*-
"""
ai_context.py —— AI 分析上下文构建模块
=========================================

从数据库提取个股的近期关键信息，生成 AI 分析所需的上下文文本。

上下文包含：
  - 最近 3 个月业绩预告（forecast 表）
  - 最近 1 个月大宗交易（block_trade 表）
  - 最近 1 个月股东户数变化趋势
  - 当日行业强度排名

使用方法：
    from ai_context import build_ai_context
    context = build_ai_context("000681.SZ", "半导体", conn)
"""

import os
import sqlite3
import logging
import pandas as pd
from datetime import datetime, timedelta

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")

log = logging.getLogger(__name__)


def _safe_read(conn, sql, params):
    """安全读取，失败返回空DataFrame"""
    try:
        return pd.read_sql(sql, conn, params=params)
    except Exception:
        return pd.DataFrame()


def get_industry_rank(industry, conn):
    """获取行业强度排名"""
    df = _safe_read(conn, """
        SELECT industry, composite_score, tier
        FROM industry_rank
        ORDER BY composite_score DESC
    """, ())
    
    if df.empty:
        return None, None
    
    matched = df[df['industry'] == industry]
    if matched.empty:
        return None, None
    
    idx = df[df['industry'] == industry].index[0]
    rank = idx + 1
    tier = matched.iloc[0]['tier']
    tier_zh = {'main': '主线', 'backup': '备选', 'avoid': '回避'}.get(tier, tier)
    
    return rank, tier_zh


def get_related_news(ts_code, stock_name, industry, conn) -> list:
    """
    获取与个股相关的最近3天快讯
    """
    three_days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    
    # 构建关键词
    keywords = [stock_name] if stock_name else []
    if industry and len(industry) < 5:
        keywords.append(industry)
    
    news_list = []
    
    if not keywords:
        return news_list
    
    try:
        for keyword in keywords:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT title, content, pub_time
                FROM news_feed
                WHERE (title LIKE ? OR content LIKE ?)
                  AND pub_time >= ?
                ORDER BY pub_time DESC
                LIMIT 5
            """, (f"%{keyword}%", f"%{keyword}%", three_days_ago))
            
            for row in cursor.fetchall():
                news_list.append({
                    "title": row[0],
                    "content": row[1],
                    "pub_time": row[2]
                })
    
    except Exception as e:
        log.info(f"读取新闻失败: {e}")
        
    return news_list


def build_ai_context(ts_code, industry, stock_name="", conn=None):
    """
    生成 AI 深度分析所需的上下文信息。
    
    参数：
        ts_code: 股票代码
        industry: 行业名称
        stock_name: 股票名称（用于匹配相关快讯）
        conn: SQLite 连接（可选）
    
    返回：
        格式化的上下文文本
    """
    _own_conn = conn is None
    if _own_conn:
        conn = sqlite3.connect(DB_PATH)
    
    try:
        context_parts = ["【个股近期动态】"]
        
        # 1. 最近 3 个月业绩预告
        three_months_ago = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
        forecast_df = _safe_read(conn, """
            SELECT type, p_change_min, p_change_max, ann_date, report_type
            FROM forecast
            WHERE ts_code = ? AND ann_date >= ?
            ORDER BY ann_date DESC LIMIT 1
        """, (ts_code, three_months_ago))
        
        if not forecast_df.empty:
            f = forecast_df.iloc[0]
            p_change_min = f['p_change_min']
            p_change_max = f['p_change_max']
            change_range = ""
            if pd.notna(p_change_min) and pd.notna(p_change_max):
                change_range = f"{p_change_min}% ~ {p_change_max}%"
            elif pd.notna(p_change_min):
                change_range = f"≥{p_change_min}%"
            elif pd.notna(p_change_max):
                change_range = f"≤{p_change_max}%"
            
            type_zh = {
                'performance': '业绩预告',
                'profit': '预盈',
                'loss': '预亏',
                'increase': '预增',
                'decrease': '预减',
                'turnaround': '扭亏'
            }.get(f['type'], f['type'])
            
            forecast_info = f"{type_zh}（{f['report_type']}），净利润变动 {change_range}（公告日: {f['ann_date']}）"
            context_parts.append(f"- 最新业绩预告：{forecast_info}")
        else:
            context_parts.append("- 最新业绩预告：无")
        
        # 2. 最近 7 天公司公告（简化版，暂无完整数据时显示无）
        context_parts.append("- 公司公告：无近期数据")
        
        # 3. 最近 3 天相关快讯
        related_news = get_related_news(ts_code, stock_name, industry, conn)
        if related_news:
            for news in related_news[:3]:
                context_parts.append(f"- 相关快讯：{news['pub_time']}，{news['title']}")
        else:
            context_parts.append("- 相关快讯：无")
        
        # 4. 最近 1 个月大宗交易
        one_month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        block_df = _safe_read(conn, """
            SELECT trade_date, price, volume, amount, buyer, seller
            FROM block_trade
            WHERE ts_code = ? AND trade_date >= ?
            ORDER BY trade_date DESC
        """, (ts_code, one_month_ago))
        
        if not block_df.empty:
            total_amount = block_df['amount'].sum() / 10000  # 万元
            avg_price = block_df['price'].mean()
            count = len(block_df)
            
            block_summary = f"共 {count} 笔，合计 {total_amount:.0f} 万元，均价 {avg_price:.2f} 元"
            
            # 分析买卖方
            has_institution = any('机构' in str(b) for b in block_df['buyer'].dropna())
            if has_institution:
                block_summary += "，机构买入"
            
            context_parts.append(f"- 近一月大宗交易：{block_summary}")
        else:
            context_parts.append("- 近一月大宗交易：无")
        
        # 5. 股东户数变化趋势
        holder_df = _safe_read(conn, """
            SELECT end_date, holder_num
            FROM stk_holdernumber
            WHERE ts_code = ?
            ORDER BY end_date DESC LIMIT 3
        """, (ts_code,))
        
        if len(holder_df) >= 2:
            latest = holder_df.iloc[0]
            prev = holder_df.iloc[1]
            
            if pd.notna(latest['holder_num']) and pd.notna(prev['holder_num']):
                chg = (latest['holder_num'] - prev['holder_num']) / prev['holder_num']
                trend = "下降" if chg < 0 else "上升"
                context_parts.append(f"- 股东户数趋势：{trend}（{chg:.1%}）")
            else:
                context_parts.append("- 股东户数趋势：数据异常")
        else:
            context_parts.append("- 股东户数趋势：数据不足")
        
        # 6. 行业强度排名
        rank, tier = get_industry_rank(industry, conn)
        if rank is not None:
            context_parts.append(f"- 行业强度：{industry} 排名第 {rank} 位（{tier}）")
        else:
            context_parts.append(f"- 行业强度：未获取到 {industry} 的排名数据")
        
        return "\n".join(context_parts)
    
    finally:
        if _own_conn:
            conn.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="AI上下文生成器")
    parser.add_argument("--ts_code", default="000681.SZ", help="股票代码")
    parser.add_argument("--industry", default="半导体", help="行业名称")
    args = parser.parse_args()
    
    context = build_ai_context(args.ts_code, args.industry)
    print(context)
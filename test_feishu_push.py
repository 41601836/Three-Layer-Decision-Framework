# -*- coding: utf-8 -*-
"""
test_feishu_push.py —— 飞书推送测试脚本
========================================

测试飞书推送功能，验证新增的数据维度是否正确显示。
"""

import os
import sys
import sqlite3
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")


def get_market_overview(conn):
    """获取整体市场分析数据"""
    overview = {}
    
    # 1. 市场环境（从 market_env 缓存读取）
    try:
        row = conn.execute("""
            SELECT mode, max_position, detail, timestamp
            FROM market_mode_cache
            ORDER BY timestamp DESC LIMIT 1
        """).fetchone()
        if row:
            overview['market_mode'] = row[0]
            overview['max_position'] = row[1]
            overview['market_detail'] = row[2]
    except Exception:
        overview['market_mode'] = 'defense'
        overview['max_position'] = 0.30
        overview['market_detail'] = '数据未更新'
    
    # 2. 行业轮动（从 industry_rank 读取）
    try:
        rows = conn.execute("""
            SELECT industry, tier, composite_score
            FROM industry_rank
            ORDER BY composite_score DESC LIMIT 10
        """).fetchall()
        main_line = [r[0] for r in rows if r[1] == 'main']
        backup_line = [r[0] for r in rows if r[1] == 'backup']
        overview['main_industries'] = main_line
        overview['backup_industries'] = backup_line
    except Exception:
        overview['main_industries'] = []
        overview['backup_industries'] = []
    
    # 3. 市场温度（上涨家数占比）
    try:
        row = conn.execute("""
            SELECT trade_date FROM daily_prices 
            ORDER BY trade_date DESC LIMIT 1
        """).fetchone()
        trade_date = row[0] if row else datetime.now().strftime("%Y%m%d")
        
        rows = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END) as up_count
            FROM daily_prices WHERE trade_date = ?
        """, (trade_date,)).fetchone()
        
        if rows and rows[0] > 0:
            overview['up_ratio'] = rows[1] / rows[0]
            overview['trade_date'] = trade_date
    except Exception:
        overview['up_ratio'] = 0.5
        overview['trade_date'] = datetime.now().strftime("%Y%m%d")
    
    # 4. 整体资金流向
    try:
        row = conn.execute("""
            SELECT SUM(net_mf_amount) as total_net
            FROM moneyflow WHERE trade_date = ?
        """, (overview.get('trade_date', ''),)).fetchone()
        overview['market_moneyflow'] = row[0] if row and row[0] else 0
    except Exception:
        overview['market_moneyflow'] = 0
    
    return overview


def get_stock_detail(conn, ts_code):
    """获取个股详细数据"""
    detail = {'ts_code': ts_code}
    
    # 1. 主力资金（moneyflow）
    try:
        row = conn.execute("""
            SELECT buy_elg_amount, buy_lg_amount, 
                   sell_elg_amount, sell_lg_amount, net_mf_amount
            FROM moneyflow 
            WHERE ts_code = ? 
            ORDER BY trade_date DESC LIMIT 1
        """, (ts_code,)).fetchone()
        if row:
            net_main = (row[0] + row[1]) - (row[2] + row[3])
            detail['net_main_flow'] = net_main  # 万元
            detail['net_mf_amount'] = row[4]
    except Exception:
        detail['net_main_flow'] = 0
        detail['net_mf_amount'] = 0
    
    # 2. 融资余额（margin_detail）
    try:
        row = conn.execute("""
            SELECT rzye, rzmcl, rzche
            FROM margin_detail 
            WHERE ts_code = ? 
            ORDER BY trade_date DESC LIMIT 1
        """, (ts_code,)).fetchone()
        if row:
            detail['margin_balance'] = row[0]  # 融资余额
            detail['margin_ratio'] = row[1]    # 融资买入额
    except Exception:
        detail['margin_balance'] = 0
        detail['margin_ratio'] = 0
    
    # 3. 北向资金（hsgt_top10）
    try:
        rows = conn.execute("""
            SELECT trade_date, hold_amount, hold_ratio
            FROM hsgt_top10 
            WHERE ts_code = ? 
            ORDER BY trade_date DESC LIMIT 5
        """, (ts_code,)).fetchall()
        if rows:
            latest = rows[0]
            prev = rows[-1] if len(rows) > 1 else rows[0]
            detail['hsgt_hold'] = latest[1]  # 持仓金额
            detail['hsgt_ratio'] = latest[2]  # 持仓占比
            detail['hsgt_change'] = latest[1] - prev[1]  # 近5日变化
    except Exception:
        detail['hsgt_hold'] = 0
        detail['hsgt_ratio'] = 0
        detail['hsgt_change'] = 0
    
    # 4. 股东户数（stk_holdernumber）
    detail['holder_num'] = 0
    detail['holder_change'] = 0
    try:
        rows = conn.execute("""
            SELECT end_date, holder_num
            FROM stk_holdernumber 
            WHERE ts_code = ? 
            ORDER BY end_date DESC LIMIT 2
        """, (ts_code,)).fetchall()
        if len(rows) >= 2:
            latest = rows[0][1]
            prev = rows[1][1]
            change_pct = (latest - prev) / prev * 100 if prev > 0 else 0
            detail['holder_num'] = latest
            detail['holder_change'] = change_pct
    except Exception:
        detail['holder_num'] = 0
        detail['holder_change'] = 0
    
    # 5. 大宗交易（block_trade）
    try:
        rows = conn.execute("""
            SELECT trade_date, price, volume, amount
            FROM block_trade 
            WHERE ts_code = ? 
            ORDER BY trade_date DESC LIMIT 5
        """, (ts_code,)).fetchall()
        if rows:
            detail['block_count'] = len(rows)
            detail['block_amount'] = sum(r[3] for r in rows) / 10000  # 万元
        else:
            detail['block_count'] = 0
            detail['block_amount'] = 0
    except Exception:
        detail['block_count'] = 0
        detail['block_amount'] = 0
    
    return detail


def send_enhanced_push():
    """发送增强版飞书推送"""
    from scripts.feishu_bot import send_stock_report, send_daily_summary, _post
    
    conn = sqlite3.connect(DB_PATH)
    
    # 1. 获取整体市场分析
    overview = get_market_overview(conn)
    
    # 2. 发送汇总摘要
    print("\n[推送 1] 整体市场汇总卡片...")
    summary_data = [
        {'ts_code': '000001.SZ', 'name': '平安银行', 'industry': '银行', 
         'total_score': 88, 'grade': '🔴 S级'},
        {'ts_code': '600519.SH', 'name': '贵州茅台', 'industry': '白酒', 
         'total_score': 82, 'grade': '🟠 A级'},
    ]
    ok1 = send_daily_summary(
        results=summary_data,
        session_name="三层漏斗分析",
        total_scanned=5000,
        trade_date=overview['trade_date']
    )
    print(f"  结果: {'✅ 成功' if ok1 else '❌ 失败'}")
    
    # 3. 获取精选股票列表（测试数据）
    test_stocks = [
        {"ts_code": "600981.SH", "name": "苏豪汇鸿", "score": 88, "industry": "贸易",
         "close": 2.58, "pct_chg": -4.44},
        {"ts_code": "603605.SH", "name": "珀莱雅", "score": 82, "industry": "美容护理",
         "close": 65.78, "pct_chg": -1.05},
    ]
    
    # 4. 发送个股详情卡片
    for stock in test_stocks:
        detail = get_stock_detail(conn, stock['ts_code'])
        
        print(f"\n[推送 2] {stock['ts_code']} {stock['name']}...")
        print(f"  主力资金: {detail['net_main_flow']:.0f}万")
        print(f"  北向资金: {detail['hsgt_change']/10000:.0f}万")
        print(f"  股东户数变化: {detail['holder_change']:+.1f}%")
        print(f"  大宗交易: {detail['block_count']}笔, {detail['block_amount']:.0f}万")
        print(f"  融资余额: {detail['margin_balance']/10000:.0f}万")
        
        # 使用系统自带的 send_stock_report 函数
        ok = send_stock_report(
            ts_code=stock['ts_code'],
            name=stock['name'],
            total_score=stock['score'],
            python_score=stock['score'] * 0.7,
            ai_score=stock['score'] * 0.3,
            report_md="## AI 分析报告\n\n**评分依据**：\n- 主力资金净流入\n- 股东户数下降\n- 行业催化评分\n\n**交易建议**：\n- 关注价格回调\n- 控制仓位在 8% 以内\n- 止损 2%",
            industry=stock['industry'],
            close_price=stock['close'],
            pct_chg=stock['pct_chg'],
            session_name="三层漏斗分析",
            trade_date=overview['trade_date']
        )
        print(f"  结果: {'✅ 成功' if ok else '❌ 失败'}")
    
    conn.close()
    
    print("\n" + "="*50)
    print("飞书推送测试完成！请检查飞书群消息验证数据显示。")


if __name__ == "__main__":
    send_enhanced_push()
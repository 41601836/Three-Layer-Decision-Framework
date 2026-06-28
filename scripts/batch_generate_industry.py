# -*- coding: utf-8 -*-
"""
批量生成行业数据
"""
import sqlite3
import time

DB_PATH = "/Users/lyu/Three-Layer-Decision-Framework/db/stock_daily.db"


def generate_industry_data(trade_date):
    """为指定日期生成行业数据"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    
    try:
        # 检查是否已存在
        exists = conn.execute("SELECT COUNT(*) FROM industry_rank WHERE calc_date=?", (trade_date,)).fetchone()[0]
        if exists > 0:
            conn.close()
            return False, "已存在"
        
        # 生成行业数据
        sql = """
        INSERT INTO industry_rank
        (calc_date, industry, composite_score, net_mf_amount, stock_count, avg_pct_chg, tier)
        SELECT 
            d.trade_date as calc_date,
            s.industry,
            AVG(d.pct_chg) as composite_score,
            SUM(COALESCE(m.net_mf_amount, 0)) / 10000.0 as net_mf_amount,
            COUNT(DISTINCT d.ts_code) as stock_count,
            AVG(d.pct_chg) as avg_pct_chg,
            'main' as tier
        FROM stock_list s
        JOIN daily_prices d ON s.ts_code = d.ts_code
        LEFT JOIN moneyflow m ON d.ts_code = m.ts_code AND d.trade_date = m.trade_date
        WHERE d.trade_date = ? AND s.industry IS NOT NULL AND s.industry != ''
        GROUP BY d.trade_date, s.industry
        ORDER BY avg_pct_chg DESC
        """
        conn.execute(sql, (trade_date,))
        conn.commit()
        
        count = conn.execute("SELECT COUNT(*) FROM industry_rank WHERE calc_date=?", (trade_date,)).fetchone()[0]
        conn.close()
        return True, f"生成 {count} 条"
    except Exception as e:
        conn.close()
        return False, str(e)


def main():
    conn = sqlite3.connect(DB_PATH)
    
    # 获取最近30个交易日期
    dates = conn.execute('SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date DESC LIMIT 30').fetchall()
    dates = [d[0] for d in dates]
    conn.close()
    
    print(f"📊 开始批量生成行业数据（共 {len(dates)} 个日期）")
    print("-" * 50)
    
    success_count = 0
    fail_count = 0
    
    for i, date in enumerate(dates):
        status, msg = generate_industry_data(date)
        if status:
            print(f"✅ [{i+1}/{len(dates)}] {date}: {msg}")
            success_count += 1
        else:
            print(f"⚠️ [{i+1}/{len(dates)}] {date}: {msg}")
            fail_count += 1
        time.sleep(0.1)  # 避免过快
    
    print("-" * 50)
    print(f"📋 完成！成功: {success_count}, 失败/跳过: {fail_count}")


if __name__ == "__main__":
    main()

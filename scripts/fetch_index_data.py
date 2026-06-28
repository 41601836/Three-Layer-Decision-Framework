# -*- coding: utf-8 -*-
"""
获取上证指数数据并写入数据库
"""
import os
import sys
import sqlite3
import tushare as ts
from datetime import datetime, timedelta

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")

TUSHARE_TOKEN = "bcfb8db101b928d1dfff5685dff95f2441d8c1b4395e2ecd067116ea"

try:
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()
except Exception as e:
    print(f"❌ Tushare 初始化失败: {e}")
    sys.exit(1)


def fetch_index_daily(index_code, start_date, end_date):
    """获取指数日线数据"""
    try:
        df = pro.index_daily(ts_code=index_code, start_date=start_date, end_date=end_date)
        return df
    except Exception as e:
        print(f"❌ 获取 {index_code} 数据失败: {e}")
        return None


def save_to_db(conn, df, index_code):
    """保存数据到数据库"""
    if df is None or df.empty:
        return 0
    
    # Tushare amount 单位是千元，换算为元
    df["amount"] = df["amount"] * 1000
    df["adj_factor"] = 1.0
    df["ts_code"] = index_code
    
    cols = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount", "adj_factor"]
    df = df[cols]
    
    rows = [
        (r.ts_code, r.trade_date,
         r.open, r.high, r.low, r.close, r.pre_close,
         r.change, r.pct_chg, r.vol, r.amount,
         r.adj_factor if r.adj_factor is not None else 1.0)
        for r in df.itertuples(index=False)
    ]
    
    conn.executemany("""
        INSERT OR REPLACE INTO daily_prices
          (ts_code, trade_date, open, high, low, close, pre_close,
           change, pct_chg, vol, amount, adj_factor)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()
    
    return len(rows)


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    
    # 获取最近30天的上证指数数据
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    
    print(f"📥 正在获取上证指数 (000001.SH) 数据: {start_date} ~ {end_date}")
    
    df = fetch_index_daily("000001.SH", start_date, end_date)
    if df is not None and not df.empty:
        count = save_to_db(conn, df, "000001.SH")
        print(f"✅ 成功写入 {count} 条上证指数数据")
        
        # 显示最新数据
        latest = df.iloc[0]
        print(f"📊 最新数据: {latest['trade_date']} - 收盘价: {latest['close']:.2f}, 涨跌幅: {latest['pct_chg']:.2f}%")
    else:
        print("❌ 未能获取上证指数数据")
    
    conn.close()


if __name__ == "__main__":
    main()

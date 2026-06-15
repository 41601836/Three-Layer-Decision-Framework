# monday_warfare/fetch_data.py
import os
import time
import logging
import sqlite3
import pandas as pd
import tushare as ts
from datetime import datetime, timedelta

logger = logging.getLogger("monday_wave")

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config_loader import get_config

token = get_config("api.tushare_token", "")
if token and "填入" not in token:
    ts.set_token(token)

pro = ts.pro_api()
RATE_LIMIT = 0.5

from .db_setup import DB_PATH, init_monday_tables

def fetch_weekly():
    conn = sqlite3.connect(DB_PATH)
    codes = pd.read_sql("SELECT ts_code FROM stock_list", conn)['ts_code'].tolist()
    total = len(codes)
    for i, code in enumerate(codes, 1):
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM weekly_prices WHERE ts_code=?", (code,))
        last_date = cur.fetchone()[0]
        start = (datetime.strptime(last_date, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d') if last_date else '20210101'
        end = datetime.now().strftime('%Y%m%d')
        if start > end:
            continue
        df = pro.weekly(ts_code=code, start_date=start, end_date=end)
        if df is not None and not df.empty:
            df.to_sql('weekly_prices', conn, if_exists='append', index=False)
            conn.commit()
        time.sleep(0.3)
        if i % 200 == 0:
            print(f"周线进度: {i}/{total}")
    conn.close()
    print("周线数据更新完成。")

def fetch_moneyflow():
    conn = sqlite3.connect(DB_PATH)
    trade_dates = pd.read_sql("SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date", conn)['trade_date'].tolist()
    for trade_date in trade_dates[-250:]:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM moneyflow_daily WHERE trade_date=?", (trade_date,))
        if cur.fetchone()[0] > 4000:
            continue
        try:
            df = pro.moneyflow(trade_date=trade_date)
            if df is not None and not df.empty:
                df['net_mf_amount'] = df['net_mf_amount'] if 'net_mf_amount' in df.columns else 0
                df.to_sql('moneyflow_daily', conn, if_exists='append', index=False)
                conn.commit()
                print(f"{trade_date} 资金流已导入")
        except Exception as e:
            print(f"{trade_date} 资金流获取失败: {e}")
        time.sleep(1.0)
    conn.close()
    print("资金流向更新完成。")

def fetch_margin():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT MAX(trade_date) FROM margin_summary")
    last = cur.fetchone()[0]
    start = (datetime.strptime(last, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d') if last else '20200101'
    end = datetime.now().strftime('%Y%m%d')
    df = pro.margin(start_date=start, end_date=end)
    if df is not None and not df.empty:
        df.rename(columns={'cal_date':'trade_date'}, inplace=True)
        df[['trade_date','rzye']].to_sql('margin_summary', conn, if_exists='append', index=False)
        conn.commit()
    conn.close()
    print("融资融券数据更新完成。")

def fetch_limits(start_date='20240101', end_date=None):
    if end_date is None:
        end_date = datetime.now().strftime('%Y%m%d')
    conn = sqlite3.connect(DB_PATH)
    trade_dates = pd.read_sql(f"SELECT DISTINCT trade_date FROM daily_prices WHERE trade_date BETWEEN '{start_date}' AND '{end_date}'", conn)['trade_date'].tolist()
    for date in trade_dates:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM limit_data WHERE trade_date=?", (date,))
        if cur.fetchone()[0] > 0:
            continue
        df = pro.limit_list_d(trade_date=date, limit_type='U,D,Z')
        if df is not None and not df.empty:
            df[['trade_date','ts_code','limit_type']].to_sql('limit_data', conn, if_exists='append', index=False)
            conn.commit()
        time.sleep(0.5)
    conn.close()
    print("涨停数据更新完成。")

def fetch_fundamentals():
    conn = sqlite3.connect(DB_PATH)
    # 资产负债表（最新一期）
    df_bs = pro.balancesheet(period='20241231')
    if df_bs is not None and not df_bs.empty:
        df_bs = df_bs[['ts_code','end_date','goodwill','total_hldr_eqy_exc_min_int']]
        df_bs.columns = ['ts_code','end_date','goodwill','total_equity']
        df_bs.to_sql('balancesheet', conn, if_exists='replace', index=False)
        conn.commit()
    # 质押
    df_pl = pro.pledge_stat()
    if df_pl is not None and not df_pl.empty:
        df_pl[['ts_code','end_date','pledge_ratio']].to_sql('pledge_stat', conn, if_exists='replace', index=False)
        conn.commit()
    # 解禁（未来一周）
    today = datetime.now().strftime('%Y%m%d')
    end_day = (datetime.now() + timedelta(days=7)).strftime('%Y%m%d')
    df_unlock = pro.share_float(start_date=today, end_date=end_day)
    if df_unlock is not None and not df_unlock.empty:
        df_unlock[['ts_code','float_date','float_ratio']].to_sql('share_unlock', conn, if_exists='append', index=False)
        conn.commit()
    # 业绩预告
    df_fc = pro.forecast(period='20241231')
    if df_fc is not None and not df_fc.empty:
        df_fc[['ts_code','end_date','type','p_change_min']].to_sql('forecast', conn, if_exists='replace', index=False)
        conn.commit()
    conn.close()
    print("基本面数据更新完成。")

def run_full_update():
    init_monday_tables()
    print("开始更新周线数据...")
    fetch_weekly()
    print("开始更新资金流向...")
    fetch_moneyflow()
    print("开始更新融资余额...")
    fetch_margin()
    print("开始更新涨停数据...")
    fetch_limits()
    print("开始更新基本面数据...")
    fetch_fundamentals()
    print("🎉 所有周一战法所需数据更新完成。")

if __name__ == '__main__':
    run_full_update()
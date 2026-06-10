# -*- coding: utf-8 -*-
"""
北向资金数据采集脚本 (HSGT Moneyflow Fetcher)
=============================================

功能：采集沪深港通资金流向数据，用于三重流出风险否决。

三重流出风险否决：
  - 主力资金流出（特大单+大单净流出）
  - 融资余额下降
  - 北向资金流出

当三者同时发生时，触发一票否决，过滤掉该信号。

使用方法：
    python scripts/fetch_hsgt.py --start 20240101 --end 20240630
"""

import os
import sys
import sqlite3
import pandas as pd
import tushare as ts
import argparse
import time
from datetime import datetime, timedelta

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from scripts.tokens import TOKEN

def get_hsgt_data(pro, start_date, end_date):
    """获取北向资金汇总数据"""
    print(f"[HSGT] 正在获取北向资金数据: {start_date} ~ {end_date}")

    df = pro.moneyflow_hsgt(start_date=start_date, end_date=end_date)

    if df is None or df.empty:
        print("[HSGT] 未获取到数据")
        return pd.DataFrame()

    print(f"[HSGT] 获取到 {len(df)} 条记录")

    # 确保必要字段存在
    needed_cols = ['trade_date', 'hgt', 'sgt']
    missing = [c for c in needed_cols if c not in df.columns]
    if missing:
        print(f"[HSGT] 警告：缺少字段 {missing}，已有字段：{list(df.columns)}")
        return pd.DataFrame()

    # 将 hgt / sgt 强制转换为数値（Tushare 有时返回字符串）
    for col in ['ggt_ss', 'ggt_sz', 'hgt', 'sgt']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)

    # 计算北向资金净流入（沪股通+深股通）
    df['north_money'] = df['hgt'] + df['sgt']

    # 保留全部字段（与已存表结构匹配）
    keep_cols = [c for c in ['trade_date', 'ggt_ss', 'ggt_sz', 'hgt', 'sgt', 'north_money']
                 if c in df.columns]
    return df[keep_cols]

def get_stock_hsgt_data(pro, start_date, end_date):
    """获取个股沪深港通持股数据"""
    print(f"[HSGT] 正在获取个股北向持仓数据: {start_date} ~ {end_date}")
    
    all_data = []
    current_date = start_date
    
    while current_date <= end_date:
        try:
            df = pro.hsgt_top10(trade_date=current_date)
            if not df.empty:
                all_data.append(df)
                print(f"  {current_date}: {len(df)} 条")
                
                # 同时获取个股北向持股明细（前100名）
                try:
                    df_detail = pro.hsgt_hold(trade_date=current_date)
                    if not df_detail.empty:
                        print(f"  {current_date}: 明细数据 {len(df_detail)} 条")
                        # 保存明细数据到临时表
                        df_detail.to_sql('hsgt_hold_temp', conn, if_exists='append', index=False)
                except Exception as e:
                    print(f"  {current_date}: 获取明细失败 - {e}")
                    
        except Exception as e:
            print(f"  {current_date}: 获取失败 - {e}")
        
        current_date = (datetime.strptime(current_date, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')
        time.sleep(0.5)
    
    if all_data:
        return pd.concat(all_data, ignore_index=True)
    return pd.DataFrame()

def get_stock_hsgt_history(pro, ts_code, start_date, end_date):
    """获取个股北向资金历史持股数据"""
    print(f"[HSGT] 正在获取个股 {ts_code} 北向持仓历史: {start_date} ~ {end_date}")
    
    try:
        # 获取个股北向持股历史数据
        df = pro.hsgt_hold(ts_code=ts_code, start_date=start_date, end_date=end_date)
        
        if df.empty:
            print(f"[HSGT] 未获取到 {ts_code} 的北向历史数据")
            return pd.DataFrame()
            
        print(f"[HSGT] 获取到 {ts_code} {len(df)} 条历史记录")
        
        # 计算5日变化
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df['vol'] = pd.to_numeric(df['vol'], errors='coerce')
        df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
        df['ratio'] = pd.to_numeric(df['ratio'], errors='coerce')
        
        # 计算5日变化
        df['ratio_5d_change'] = df['ratio'].diff(5)
        df['vol_5d_change'] = df['vol'].diff(5)
        
        return df
        
    except Exception as e:
        print(f"[HSGT] 获取个股历史数据失败 {ts_code}: {e}")
        return pd.DataFrame()

def save_to_db(df, table_name, conn):
    """保存数据到数据库，自动去重（以 trade_date 为准）"""
    if df is None or df.empty:
        return

    # 删除已存在的同日期数据，再插入（upsert式）
    dates = df['trade_date'].unique().tolist()
    placeholders = ','.join(['?' for _ in dates])
    cursor = conn.cursor()
    try:
        cursor.execute(f"DELETE FROM {table_name} WHERE trade_date IN ({placeholders})", dates)
        conn.commit()
    except Exception:
        pass  # 表尚不存在时跳过

    df.to_sql(table_name, conn, if_exists='append', index=False)
    conn.commit()
    print(f"[HSGT] 已保存 {len(df)} 条数据到 {table_name}")

def check_2025_data(conn):
    """检查hsgt_moneyflow中2025年记录数"""
    try:
        r = conn.execute("SELECT COUNT(*) FROM hsgt_moneyflow WHERE trade_date LIKE '2025%'").fetchone()
        return r[0] if r else 0
    except Exception:
        return 0


def main():
    parser = argparse.ArgumentParser(description="北向资金数据采集")
    parser.add_argument("--start", default="20250101", help="开始日期（默认2025年初）")
    parser.add_argument("--end",   default=datetime.now().strftime('%Y%m%d'), help="结束日期")
    parser.add_argument("--stock", action='store_true', help="同时获取个股持仓数据")
    parser.add_argument("--check", action='store_true', help="仅检查当前数据量，不拉取")
    parser.add_argument("--history", help="获取指定个股的北向历史数据（格式：600519.SH）")
    args = parser.parse_args()

    db_path = os.path.join(ROOT_DIR, "db", "stock_daily.db")
    conn = sqlite3.connect(db_path)
    
    # 全局conn变量，供get_stock_hsgt_data使用
    global conn

    if args.check:
        cnt = check_2025_data(conn)
        print(f"[CHECK] hsgt_moneyflow 中2025年记录数: {cnt}")
        if cnt >= 200:
            print("[CHECK] OK: 数据充足，北向否决机制可用")
        else:
            print(f"[CHECK] WARN: 数据不足（当前{cnt}条，预期应超过200条），需要补拉")
        conn.close()
        return

    ts.set_token(TOKEN)
    pro = ts.pro_api()

    before_cnt = check_2025_data(conn)
    print(f"[INFO] 补拉前2025年已有数据: {before_cnt} 条")
    
    # 获取个股历史数据
    if args.history:
        ts_code = args.history
        # 获取最近5日数据
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
        
        df_history = get_stock_hsgt_history(pro, ts_code, start_date, end_date)
        
        if not df_history.empty:
            # 保存到数据库
            df_history.to_sql('hsgt_stock_history', conn, if_exists='replace', index=False)
            print(f"[HSGT] 已保存 {ts_code} 北向历史数据到 hsgt_stock_history")
            
            # 显示5日变化
            latest = df_history.iloc[0]
            if 'ratio_5d_change' in df_history.columns and not pd.isna(latest['ratio_5d_change']):
                print(f"[HSGT] 5日持股比例变化: {latest['ratio_5d_change']:.4f}%")
            if 'vol_5d_change' in df_history.columns and not pd.isna(latest['vol_5d_change']):
                print(f"[HSGT] 5日持股数量变化: {latest['vol_5d_change']:,} 股")
                
        conn.close()
        return

    hsgt_df = get_hsgt_data(pro, args.start, args.end)
    save_to_db(hsgt_df, 'hsgt_moneyflow', conn)

    if args.stock:
        stock_df = get_stock_hsgt_data(pro, args.start, args.end)
        save_to_db(stock_df, 'hsgt_top10', conn)
        
        # 创建北向持股明细表（如果不存在）
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hsgt_hold (
                    trade_date VARCHAR(8),
                    ts_code VARCHAR(15),
                    name VARCHAR(50),
                    close FLOAT,
                    vol BIGINT,
                    amount FLOAT,
                    ratio FLOAT,
                    PRIMARY KEY (trade_date, ts_code)
                )
            """)
            conn.commit()
            
            # 从临时表导入数据
            conn.execute("""
                INSERT OR REPLACE INTO hsgt_hold 
                SELECT trade_date, ts_code, name, close, vol, amount, ratio 
                FROM hsgt_hold_temp
            """)
            conn.commit()
            print("[HSGT] 已合并明细数据到 hsgt_hold 表")
        except Exception as e:
            print(f"[HSGT] 创建/合并明细表失败: {e}")

    after_cnt = check_2025_data(conn)
    print(f"[VERIFY] 补拉后2025年数据: {after_cnt} 条")
    if after_cnt >= 200:
        print("[VERIFY] OK: 北向资金数据充足，三重否决机制已激活")
    else:
        print(f"[VERIFY] WARN: 仅 {after_cnt} 条，预期超过200条，请检查Tushare权限")

    conn.close()
    print("[HSGT] 北向资金数据采集完成")


if __name__ == "__main__":
    main()
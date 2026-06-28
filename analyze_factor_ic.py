# -*- coding: utf-8 -*-
"""
因子IC分析脚本
================================================
本脚本用于分析各因子与未来收益的相关性（IC值），评估因子有效性。
"""

import os
import sys
import sqlite3
import argparse
import time
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")
sys.path.insert(0, ROOT_DIR)


def log_info(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [INFO] {msg}")


def load_daily_data(conn, start_date, end_date):
    pre_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
    query = """
        SELECT dp.ts_code, dp.trade_date, dp.open, dp.high, dp.low, dp.close, dp.pct_chg, dp.vol, dp.amount, sl.industry
        FROM daily_prices dp
        LEFT JOIN stock_list sl ON dp.ts_code = sl.ts_code
        WHERE dp.trade_date BETWEEN ? AND ?
        ORDER BY dp.ts_code, dp.trade_date
    """
    df = pd.read_sql(query, conn, params=(pre_start, end_date))
    df["trade_date"] = df["trade_date"].astype(str)
    return df


def load_moneyflow_data(conn, start_date, end_date):
    query = """
        SELECT ts_code, trade_date,
               buy_elg_amount, sell_elg_amount,
               buy_lg_amount, sell_lg_amount
        FROM moneyflow
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """
    df = pd.read_sql(query, conn, params=(start_date, end_date))
    df["trade_date"] = df["trade_date"].astype(str)
    return df


def load_chips_data(conn, start_date, end_date):
    query = """
        SELECT ts_code, trade_date, price, percent
        FROM cyq_chips
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """
    df = pd.read_sql(query, conn, params=(start_date, end_date))
    df["trade_date"] = df["trade_date"].astype(str)
    return df


def compute_future_return(daily_df, days=5):
    daily_df = daily_df.sort_values(["ts_code", "trade_date"])
    daily_df["future_return"] = daily_df.groupby("ts_code")["pct_chg"].shift(-days) / 100.0
    return daily_df


def compute_ic(df, factor_col, return_col="future_return"):
    valid_data = df[[factor_col, return_col]].dropna()
    if len(valid_data) < 10:
        return np.nan
    return valid_data[factor_col].corr(valid_data[return_col], method="spearman")


def compute_factors(daily_df, money_df, chips_df):
    df = daily_df.copy()
    
    df["net_main_wan"] = (money_df["buy_lg_amount"] - money_df["sell_lg_amount"]).fillna(0)
    df["amount_yuan"] = df["amount"] * 1000
    
    df["net_main_intensity"] = (df["net_main_wan"] * 10000.0) / df["amount_yuan"]
    df["net_main_intensity"] = df["net_main_intensity"].fillna(0)
    
    df["winner_rate"] = 1.0
    df["chips_peak_pct"] = 25.0
    
    if not chips_df.empty:
        chips_merged = chips_df.merge(df[["ts_code", "trade_date", "close"]], 
                                      on=["ts_code", "trade_date"], how="inner")
        chips_merged["is_win"] = chips_merged["price"] <= chips_merged["close"]
        
        winner_df = chips_merged.groupby(["ts_code", "trade_date"]).apply(
            lambda x: x[x["is_win"]]["percent"].sum() / 100.0
        ).reset_index(name="calc_winner_rate")
        
        peak_df = chips_df.groupby(["ts_code", "trade_date"])["percent"].max().reset_index(name="calc_chips_peak")
        
        df = df.merge(winner_df, on=["ts_code", "trade_date"], how="left")
        df = df.merge(peak_df, on=["ts_code", "trade_date"], how="left")
        
        has_winner = df["calc_winner_rate"].notna()
        df.loc[has_winner, "winner_rate"] = df.loc[has_winner, "calc_winner_rate"]
        
        has_peak = df["calc_chips_peak"].notna()
        df.loc[has_peak, "chips_peak_pct"] = df.loc[has_peak, "calc_chips_peak"]
        
        df.drop(columns=["calc_winner_rate", "calc_chips_peak"], errors="ignore", inplace=True)
    
    return df


def main():
    parser = argparse.ArgumentParser(description="因子IC分析")
    parser.add_argument("--start", required=True, help="开始日期(YYYYMMDD)")
    parser.add_argument("--end", required=True, help="结束日期(YYYYMMDD)")
    parser.add_argument("--days", type=int, default=5, help="未来收益天数")
    args = parser.parse_args()

    log_info(f"开始因子IC分析: {args.start} ~ {args.end}")
    
    conn = sqlite3.connect(DB_PATH)
    
    try:
        log_info("加载日线数据...")
        daily_df = load_daily_data(conn, args.start, args.end)
        log_info(f"  日线数据: {len(daily_df)} 条")
        
        log_info("加载资金流数据...")
        money_df = load_moneyflow_data(conn, args.start, args.end)
        log_info(f"  资金流数据: {len(money_df)} 条")
        
        log_info("加载筹码数据...")
        chips_df = load_chips_data(conn, args.start, args.end)
        log_info(f"  筹码数据: {len(chips_df)} 条")
        
        log_info("计算未来收益...")
        daily_df = compute_future_return(daily_df, days=args.days)
        
        log_info("合并数据...")
        merged_df = daily_df.merge(money_df, on=["ts_code", "trade_date"], how="left")
        
        log_info("计算因子...")
        merged_df = compute_factors(merged_df, money_df, chips_df)
        
        merged_df = merged_df[merged_df["trade_date"] >= args.start].copy()
        log_info(f"有效样本数: {len(merged_df)}")
        
        trade_dates = sorted(merged_df["trade_date"].unique())
        ic_results = []
        
        log_info("计算每日IC值...")
        for date in trade_dates:
            daily_data = merged_df[merged_df["trade_date"] == date]
            
            ic_winner = compute_ic(daily_data, "winner_rate")
            ic_peak = compute_ic(daily_data, "chips_peak_pct")
            ic_main = compute_ic(daily_data, "net_main_intensity")
            ic_pct = compute_ic(daily_data, "pct_chg")
            
            ic_results.append({
                "trade_date": date,
                "ic_winner_rate": ic_winner,
                "ic_chips_peak": ic_peak,
                "ic_net_main": ic_main,
                "ic_pct_chg": ic_pct,
                "sample_size": len(daily_data)
            })
        
        ic_df = pd.DataFrame(ic_results)
        
        print("\n" + "="*70)
        print("因子IC分析结果")
        print("="*70)
        print(f"分析区间: {args.start} ~ {args.end}")
        print(f"未来收益周期: {args.days} 天")
        print(f"有效交易日: {len(ic_df)}")
        print("\n【因子IC统计】")
        print("-"*70)
        
        factors = [
            ("ic_winner_rate", "获利盘占比"),
            ("ic_chips_peak", "筹码峰集中度"),
            ("ic_net_main", "主力净流入强度"),
            ("ic_pct_chg", "当日涨幅")
        ]
        
        for col, name in factors:
            values = ic_df[col].dropna()
            if len(values) == 0:
                print(f"{name}: 无有效数据")
                continue
            
            mean_ic = values.mean()
            std_ic = values.std()
            abs_mean = np.abs(values).mean()
            positive_ratio = (values > 0).mean() * 100
            t_stat = mean_ic / (std_ic / np.sqrt(len(values))) if std_ic > 0 else np.nan
            
            print(f"\n{name}:")
            print(f"  平均IC: {mean_ic:+.4f}")
            print(f"  IC标准差: {std_ic:.4f}")
            print(f"  平均绝对IC: {abs_mean:.4f}")
            print(f"  正IC比例: {positive_ratio:.1f}%")
            print(f"  t统计量: {t_stat:.2f}")
        
        print("\n" + "="*70)
        
        ic_df.to_csv(f"factor_ic_results_{args.start}_{args.end}.csv", index=False)
        log_info(f"结果已保存到 factor_ic_results_{args.start}_{args.end}.csv")
        
    finally:
        conn.close()


if __name__ == "__main__":
    main()
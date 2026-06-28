import os
import pandas as pd
import numpy as np
import sqlite3
import argparse

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")

def calculate_ic(df, factor_name, target_col='ret_5d'):
    valid_df = df.dropna(subset=[factor_name, target_col])
    if len(valid_df) < 100:
        return np.nan, np.nan
    
    ic = valid_df[[factor_name, target_col]].corr().iloc[0, 1]
    ic_std = np.std([valid_df[valid_df['trade_date'] == d][[factor_name, target_col]].corr().iloc[0, 1] 
                     for d in valid_df['trade_date'].unique() if len(valid_df[valid_df['trade_date'] == d]) >= 10])
    
    return ic, ic_std

def calculate_group_return(df, factor_name, target_col='ret_5d', n_groups=5):
    valid_df = df.dropna(subset=[factor_name, target_col])
    if len(valid_df) < n_groups * 10:
        return None
    
    valid_df['group'] = valid_df.groupby('trade_date')[factor_name].transform(
        lambda x: pd.qcut(x, n_groups, labels=False, duplicates='drop')
    )
    
    group_returns = valid_df.groupby('group')[target_col].mean() * 100
    return group_returns

def main():
    parser = argparse.ArgumentParser(description='单因子测试')
    parser.add_argument('--start', type=str, default='20240101', help='开始日期')
    parser.add_argument('--end', type=str, default='20251231', help='结束日期')
    args = parser.parse_args()
    
    conn = sqlite3.connect(DB_PATH)
    
    print(f"[LOAD] 加载日线数据...")
    daily_df = pd.read_sql("""
        SELECT ts_code, trade_date, close, pct_chg, vol 
        FROM daily_prices 
        WHERE trade_date >= ? AND trade_date <= ?
        ORDER BY ts_code, trade_date
    """, conn, params=(args.start, args.end))
    daily_df['trade_date'] = pd.to_datetime(daily_df['trade_date'])
    
    print(f"[FACTOR] 计算低波因子...")
    daily_df['ret_20d_std'] = daily_df.groupby('ts_code')['pct_chg'].transform(lambda x: x.rolling(20).std())
    daily_df['ret_60d_std'] = daily_df.groupby('ts_code')['pct_chg'].transform(lambda x: x.rolling(60).std())
    
    print(f"[FACTOR] 计算超跌因子...")
    daily_df['ret_20d'] = daily_df.groupby('ts_code')['close'].transform(lambda x: x.pct_change(20))
    daily_df['drawdown_20d'] = daily_df.groupby('ts_code')['close'].transform(
        lambda x: (x - x.rolling(20).max()) / x.rolling(20).max()
    )
    
    print(f"[FACTOR] 计算目标收益...")
    daily_df['ret_5d'] = daily_df.groupby('ts_code')['close'].transform(lambda x: x.pct_change(5).shift(-5))
    
    print(f"[LOAD] 加载财务数据...")
    try:
        finance_df = pd.read_sql("SELECT ts_code, end_date, roe, gross_margin FROM fina_main WHERE end_date >= ?", 
                                conn, params=(args.start,))
        finance_df['end_date'] = pd.to_datetime(finance_df['end_date'])
        daily_df = daily_df.merge(finance_df, on='ts_code', how='left')
        daily_df['roe'] = daily_df.groupby('ts_code')['roe'].fillna(method='ffill')
        daily_df['gross_margin'] = daily_df.groupby('ts_code')['gross_margin'].fillna(method='ffill')
    except Exception as e:
        print(f"  [WARN] 财务数据加载失败: {e}")
    
    print(f"[LOAD] 加载股东户数数据...")
    try:
        holder_df = pd.read_sql("SELECT ts_code, end_date, holder_num FROM holders WHERE end_date >= ?", 
                               conn, params=(args.start,))
        holder_df['end_date'] = pd.to_datetime(holder_df['end_date'])
        daily_df = daily_df.merge(holder_df, left_on=['ts_code', 'trade_date'], right_on=['ts_code', 'end_date'], how='left')
        daily_df['holder_num_change'] = daily_df.groupby('ts_code')['holder_num'].pct_change()
    except Exception as e:
        print(f"  [WARN] 股东户数数据加载失败: {e}")
    
    print("\n" + "="*70)
    print("单因子IC测试结果 (训练集: 2024-2025)")
    print("="*70)
    
    factors = [
        ('ret_20d_std', '20日收益率标准差'),
        ('ret_60d_std', '60日收益率标准差'),
        ('ret_20d', '20日收益率'),
        ('drawdown_20d', '20日回撤'),
        ('roe', 'ROE'),
        ('gross_margin', '毛利率'),
        ('holder_num_change', '股东户数变动')
    ]
    
    results = []
    for factor_name, factor_label in factors:
        if factor_name not in daily_df.columns:
            print(f"\n【{factor_label}】")
            print(f"  [SKIP] 因子数据缺失")
            results.append({
                '因子': factor_label,
                'IC': '-',
                'IC_std': '-'
            })
            continue
        
        ic, ic_std = calculate_ic(daily_df, factor_name)
        group_returns = calculate_group_return(daily_df, factor_name)
        
        results.append({
            '因子': factor_label,
            'IC': f"{ic:.3f}",
            'IC_std': f"{ic_std:.3f}"
        })
        
        print(f"\n【{factor_label}】")
        print(f"  IC值: {ic:.4f} (标准差: {ic_std:.4f})")
        if group_returns is not None:
            print(f"  分组收益(%):")
            for i in range(len(group_returns)):
                print(f"    组{i+1}: {group_returns.iloc[i]:.2f}%")
            print(f"  多空收益差: {(group_returns.iloc[-1] - group_returns.iloc[0]):.2f}%")
    
    results_df = pd.DataFrame(results)
    print("\n" + "="*70)
    print(results_df.to_string(index=False))
    print("="*70)
    
    print("\n" + "="*70)
    print("在验证集(2026年)上测试...")
    print("="*70)
    
    valid_df = pd.read_sql("""
        SELECT ts_code, trade_date, close, pct_chg, vol 
        FROM daily_prices 
        WHERE trade_date >= '20260101' AND trade_date <= '20260625'
        ORDER BY ts_code, trade_date
    """, conn)
    valid_df['trade_date'] = pd.to_datetime(valid_df['trade_date'])
    
    valid_df['ret_20d_std'] = valid_df.groupby('ts_code')['pct_chg'].transform(lambda x: x.rolling(20).std())
    valid_df['ret_60d_std'] = valid_df.groupby('ts_code')['pct_chg'].transform(lambda x: x.rolling(60).std())
    valid_df['ret_20d'] = valid_df.groupby('ts_code')['close'].transform(lambda x: x.pct_change(20))
    valid_df['drawdown_20d'] = valid_df.groupby('ts_code')['close'].transform(
        lambda x: (x - x.rolling(20).max()) / x.rolling(20).max()
    )
    valid_df['ret_5d'] = valid_df.groupby('ts_code')['close'].transform(lambda x: x.pct_change(5).shift(-5))
    
    valid_results = []
    for factor_name, factor_label in factors:
        if factor_name not in valid_df.columns:
            valid_results.append({
                '因子': factor_label,
                'IC': '-',
                'IC_std': '-'
            })
            continue
        
        ic, ic_std = calculate_ic(valid_df, factor_name)
        valid_results.append({
            '因子': factor_label,
            'IC': f"{ic:.3f}",
            'IC_std': f"{ic_std:.3f}"
        })
    
    valid_results_df = pd.DataFrame(valid_results)
    print(valid_results_df.to_string(index=False))
    print("="*70)
    
    conn.close()

if __name__ == '__main__':
    main()
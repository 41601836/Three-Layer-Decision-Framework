# -*- coding: utf-8 -*-
"""
向量化回测引擎 (Vectorized Backtest Engine) v3.2
==================================================

核心思想：抛弃逐日循环，利用 pandas 的窗口函数和矩阵运算，
一次性计算全市场所有股票在回测区间内每个交易日的信号和未来收益。

性能优势：速度比传统循环快 10 倍以上，可以在几分钟内完成全市场多年的回测。

设计架构：
  1. 数据层：批量加载全市场日线、资金流、股东、融资、北向资金数据
  2. 指标层：向量化计算各项因子
  3. 信号层：根据 v3.2 评分规则生成信号
  4. 风险层：三重流出风险否决（主力+融资+北向）
  5. 收益层：计算未来 N 日收益
  6. 聚合层：统计胜率、盈亏比、夏普比率、最大回撤等指标

v3.2 评分规则（振幅正向因子 + 权重再平衡）：
  - 20日振幅 < 15% → +8 分（强收敛）
  - 20日振幅 15~25% → +5 分（收敛）
  - 20日振幅 25~30% → +2 分（偏收敛）
  - 20日振幅 > 30% → 0 分
  - 主力资金净流入 > 0 → +12 分
  - 股东户数连续下降 → +15 分
  - 连续3日主力净流入且股价未涨 → +5 分
  - 融资余额低位（分位 < 30%）→ +3 分

信号门槛：
  - 强信号：总分 >= 30
  - 中信号：总分 >= 20

三重流出风险否决：
  - 主力资金流出 + 融资余额下降 + 北向资金流出 → 一票否决

使用方法：
    python backtest_vectorized.py --start 20250101 --end 20251231 --output results.csv
"""

import os
import sys
import sqlite3
import pandas as pd
import argparse
import time
import numpy as np

def load_data(conn, start_date, end_date):
    """批量加载回测所需数据"""
    print("[LOAD] 正在加载日线数据...")
    daily = pd.read_sql("""
        SELECT ts_code, trade_date, close, high, low, pct_chg
        FROM daily_prices
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(start_date, end_date))
    
    print("[LOAD] 正在加载资金流数据...")
    money = pd.read_sql("""
        SELECT ts_code, trade_date, buy_elg_amount, buy_lg_amount,
               sell_elg_amount, sell_lg_amount
        FROM moneyflow
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(start_date, end_date))
    
    print("[LOAD] 正在加载股东户数数据...")
    holder = pd.read_sql("""
        SELECT ts_code, ann_date, holder_num
        FROM stk_holdernumber
        ORDER BY ts_code, ann_date
    """, conn)
    
    print("[LOAD] 正在加载融资融券数据...")
    margin = pd.read_sql("""
        SELECT ts_code, trade_date, rzye
        FROM margin_detail
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(start_date, end_date))
    
    print("[LOAD] 正在加载北向资金数据...")
    hsgt = pd.read_sql("""
        SELECT trade_date, north_money
        FROM hsgt_moneyflow
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY trade_date
    """, conn, params=(start_date, end_date))
    
    return daily, money, holder, margin, hsgt

def compute_factors(daily, money, holder, margin, hsgt):
    """向量化计算各项因子（v3.2修复：振幅用向量化rolling max/min）"""
    print("[FACTOR] 计算 20 日振幅因子（向量化）...")
    # 修复：rolling().apply()无法在lambda内访问多列，改用向量化方式
    daily = daily.sort_values(['ts_code', 'trade_date'])
    daily['high_20d'] = daily.groupby('ts_code')['high'].transform(
        lambda x: x.rolling(20).max()
    )
    daily['low_20d'] = daily.groupby('ts_code')['low'].transform(
        lambda x: x.rolling(20).min()
    )
    daily['amp_20d'] = (daily['high_20d'] - daily['low_20d']) / daily['low_20d'].replace(0, float('nan'))
    daily.drop(columns=['high_20d', 'low_20d'], inplace=True)
    
    print("[FACTOR] 计算主力资金净流入...")
    money['net_main'] = (money['buy_elg_amount'] + money['buy_lg_amount'] -
                         money['sell_elg_amount'] - money['sell_lg_amount'])
    money['money_ok'] = money['net_main'] > 0
    money['money_out'] = money['net_main'] < 0
    
    print("[FACTOR] 计算连续 3 日主力净流入...")
    money = money.sort_values(['ts_code', 'trade_date'])
    money['money_3d_ok'] = money.groupby('ts_code')['money_ok'].transform(
        lambda x: x.rolling(3).sum()
    ) == 3
    
    print("[FACTOR] 计算 3 日累计涨幅...")
    daily['pct_3d_sum'] = daily.groupby('ts_code')['pct_chg'].transform(
        lambda x: x.rolling(3).sum()
    )
    
    print("[FACTOR] 计算股东户数连续下降...")
    holder = holder.sort_values(['ts_code', 'ann_date'])
    holder['holder_chg'] = holder.groupby('ts_code')['holder_num'].pct_change()
    holder['holder_ok'] = holder['holder_chg'] < 0
    holder['holder_2d_ok'] = holder.groupby('ts_code')['holder_ok'].transform(
        lambda x: x.rolling(2).sum()
    ) >= 2
    
    print("[FACTOR] 计算融资余额分位...")
    margin['rzye_pct_rank'] = margin.groupby('ts_code')['rzye'].rank(pct=True)
    margin['margin_low'] = margin['rzye_pct_rank'] < 0.3
    
    print("[FACTOR] 计算融资余额变化...")
    margin['rzye_chg'] = margin.groupby('ts_code')['rzye'].diff()
    margin['margin_down'] = margin['rzye_chg'] < 0
    
    print("[FACTOR] 计算北向资金变化...")
    hsgt['north_chg'] = hsgt['north_money'].diff()
    hsgt['hsgt_out'] = hsgt['north_chg'] < 0
    
    return daily, money, holder, margin, hsgt

def compute_future_returns(daily, periods=[5, 10, 20]):
    """利用窗口函数计算未来收益"""
    print("[RETURN] 计算未来 N 日收益...")
    daily = daily.sort_values(['ts_code', 'trade_date'])
    
    for p in periods:
        daily[f'close_{p}d'] = daily.groupby('ts_code')['close'].shift(-p)
        daily[f'ret_{p}d'] = (daily[f'close_{p}d'] / daily['close'] - 1) * 100
    
    return daily

def generate_signals(daily, money, holder, margin, hsgt):
    """向量化生成信号（v3.2 新规则）"""
    print("[SIGNAL] 合并数据...")
    
    df = daily.merge(money, on=['ts_code', 'trade_date'], how='left')
    
    holder_latest = holder.sort_values(['ts_code', 'ann_date']).groupby('ts_code').last().reset_index()
    df = df.merge(holder_latest[['ts_code', 'holder_ok', 'holder_2d_ok']], on='ts_code', how='left')
    
    margin_latest = margin.sort_values(['ts_code', 'trade_date']).groupby('ts_code').last().reset_index()
    df = df.merge(margin_latest[['ts_code', 'margin_low', 'margin_down']], on='ts_code', how='left')
    
    df = df.merge(hsgt[['trade_date', 'hsgt_out']], on='trade_date', how='left')
    
    print("[SIGNAL] 计算 v3.2 得分（振幅正向因子 + 权重再平衡）...")
    df['score_v3_1'] = 0
    
    # 因子1：振幅收敛正向分档加分（IC=0.3096，权重最高）
    df.loc[df['amp_20d'] < 0.15, 'score_v3_1'] += 8   # 强收敛 +8
    df.loc[(df['amp_20d'] >= 0.15) & (df['amp_20d'] < 0.25), 'score_v3_1'] += 5   # 收敛 +5
    df.loc[(df['amp_20d'] >= 0.25) & (df['amp_20d'] < 0.30), 'score_v3_1'] += 2   # 偏收敛 +2
    # >30% 不加分（默认为0）
    
    # 因子2：主力资金净流入 +12
    df.loc[df['money_ok'] == True, 'score_v3_1'] += 12
    
    # 因子3：股东户数连续下降 +15
    df.loc[df['holder_2d_ok'] == True, 'score_v3_1'] += 15
    
    # 因子4：三日背离 +5（原+10，基于IC排名调整）
    df.loc[(df['money_3d_ok'] == True) & (df['pct_3d_sum'] < 0), 'score_v3_1'] += 5
    
    # 因子5：融资低位 +3（原+5，辅助因子）
    df.loc[df['margin_low'] == True, 'score_v3_1'] += 3
    
    print("[SIGNAL] 三重流出风险否决...")
    df['risk_flag'] = (df['money_out'] == True) & (df['margin_down'] == True) & (df['hsgt_out'] == True)
    df.loc[df['risk_flag'] == True, 'score_v3_1'] = 0
    
    df['signal'] = 'none'
    df.loc[df['score_v3_1'] >= 30, 'signal'] = 'strong'
    df.loc[(df['score_v3_1'] >= 20) & (df['score_v3_1'] < 30), 'signal'] = 'medium'
    
    return df

def calculate_sharpe_ratio(returns, risk_free_rate=0.02):
    """计算夏普比率"""
    if len(returns) < 2:
        return 0.0
    daily_returns = returns / 100
    excess_returns = daily_returns - (risk_free_rate / 252)
    return np.sqrt(252) * excess_returns.mean() / excess_returns.std() if excess_returns.std() > 0 else 0.0

def calculate_max_drawdown(returns):
    """计算最大回撤"""
    if len(returns) < 2:
        return 0.0
    cumulative = (1 + returns / 100).cumprod()
    peak = cumulative.cummax()
    drawdown = (cumulative - peak) / peak
    return drawdown.min() * 100

def analyze_results(df):
    """分析回测结果"""
    print("\n" + "="*70)
    print("                    向量化回测结果分析 (v3.1)")
    print("="*70)
    
    df_valid = df.dropna(subset=['ret_10d'])
    total_trading_days = df_valid['trade_date'].nunique()
    
    print(f"\n【回测概览】")
    print(f"  回测区间: {df_valid['trade_date'].min()} ~ {df_valid['trade_date'].max()}")
    print(f"  交易日数: {total_trading_days} 天")
    print(f"  总信号数: {len(df_valid)} 条")
    
    print("\n【信号分布】")
    signal_counts = df_valid['signal'].value_counts()
    for sig, cnt in signal_counts.items():
        pct = cnt / len(df_valid) * 100
        print(f"  {sig}: {cnt:,} 条 ({pct:.1f}%)")
    
    print("\n【按信号强度统计】")
    for sig in ['strong', 'medium', 'none']:
        subset = df_valid[df_valid['signal'] == sig]
        if len(subset) == 0:
            continue
        win_5 = (subset['ret_5d'] > 0).mean() * 100
        win_10 = (subset['ret_10d'] > 0).mean() * 100
        win_20 = (subset['ret_20d'] > 0).mean() * 100
        avg_5 = subset['ret_5d'].mean()
        avg_10 = subset['ret_10d'].mean()
        avg_20 = subset['ret_20d'].mean()
        win_pct = subset[subset['ret_10d'] > 0]['ret_10d'].mean()
        lose_pct = subset[subset['ret_10d'] <= 0]['ret_10d'].mean()
        win_loss_ratio = abs(win_pct / lose_pct) if lose_pct != 0 else 0
        
        print(f"\n  {sig.upper()} 信号:")
        print(f"    样本数: {len(subset):,}")
        print(f"    5日胜率: {win_5:.1f}% | 5日均收益: {avg_5:.2f}%")
        print(f"   10日胜率: {win_10:.1f}% | 10日均收益: {avg_10:.2f}%")
        print(f"   20日胜率: {win_20:.1f}% | 20日均收益: {avg_20:.2f}%")
        print(f"    盈亏比: {win_loss_ratio:.2f}")
        if sig == 'strong':
            sharpe = calculate_sharpe_ratio(subset['ret_10d'])
            max_dd = calculate_max_drawdown(subset['ret_10d'])
            print(f"    夏普比率: {sharpe:.2f}")
            print(f"    最大回撤: {max_dd:.2f}%")
    
    print("\n【按得分区间统计】")
    bins = [-10, 0, 15, 30, 35]
    labels = ['<0', '0-14', '15-29', '>=30']
    df_valid['score_bin'] = pd.cut(df_valid['score_v3_1'], bins=bins, labels=labels)
    
    for label in labels:
        subset = df_valid[df_valid['score_bin'] == label]
        if len(subset) == 0:
            continue
        win_10 = (subset['ret_10d'] > 0).mean() * 100
        avg_10 = subset['ret_10d'].mean()
        print(f"  得分 {label}: 样本{len(subset):,} | 10日胜率{win_10:.1f}% | 10日均收益{avg_10:.2f}%")
    
    print("\n【月度分布分析】")
    df_valid['month'] = df_valid['trade_date'].str[:6]
    monthly_stats = df_valid.groupby('month').agg({
        'signal': ['count', lambda x: (x == 'strong').sum()],
        'ret_10d': ['mean', lambda x: (x > 0).mean() * 100]
    })
    monthly_stats.columns = ['总信号', '强信号', '10日均收益', '10日胜率']
    print(monthly_stats.round(2))
    
    print("\n【风险否决统计】")
    risk_count = df_valid['risk_flag'].sum()
    print(f"  触发三重流出否决: {risk_count:,} 次")
    print(f"  否决占比: {(risk_count / len(df_valid) * 100):.1f}%")
    
    return df_valid

def main():
    parser = argparse.ArgumentParser(description="向量化回测引擎 v3.2 (振幅正向因子)")
    parser.add_argument("--start", default="20250101", help="开始日期")
    parser.add_argument("--end", default="20251231", help="结束日期")
    parser.add_argument("--output", help="输出结果到文件")
    args = parser.parse_args()
    
    start_time = time.time()
    
    conn = sqlite3.connect("db/stock_daily.db")
    print(f"[START] 向量化回测开始，区间: {args.start} ~ {args.end}")
    
    daily, money, holder, margin, hsgt = load_data(conn, args.start, args.end)
    print(f"[DATA] 日线: {len(daily):,} | 资金流: {len(money):,} | 股东: {len(holder):,} | 融资: {len(margin):,} | 北向: {len(hsgt):,}")
    
    daily, money, holder, margin, hsgt = compute_factors(daily, money, holder, margin, hsgt)
    daily = compute_future_returns(daily)
    
    signals = generate_signals(daily, money, holder, margin, hsgt)
    
    results = analyze_results(signals)
    
    if args.output:
        results.to_csv(args.output, index=False, encoding='utf-8-sig')
        print(f"\n[OUTPUT] 结果已保存到 {args.output}")
    
    conn.close()
    
    elapsed = time.time() - start_time
    print(f"\n[DONE] 回测完成，耗时: {elapsed:.2f} 秒")

if __name__ == "__main__":
    main()
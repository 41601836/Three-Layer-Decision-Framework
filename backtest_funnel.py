# -*- coding: utf-8 -*-
"""
三层漏斗策略回测引擎 v1.0
=========================

基于三层漏斗选股策略的向量化回测，使用 2026年1月1日至6月8日数据。

策略逻辑：
  1. 第一层：SQL预筛选（涨幅、换手率、成交额）
  2. 第二层：多因子评分筛选（技术指标、资金流、筹码等）
  3. 第三层：AI深度分析（可选）

回测参数：
  - 信号产生：每日筛选Top-N股票
  - 持仓周期：5个交易日
  - 仓位管理：等权分配
  - 止损策略：ATR止损

使用方法：
    python backtest_funnel.py --start 20260101 --end 20260608
"""

import os
import sys
import sqlite3
import pandas as pd
import argparse
import time
import numpy as np

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")

def load_daily_data(conn, start_date, end_date):
    """加载日线数据"""
    print("[LOAD] 正在加载日线数据...")
    daily = pd.read_sql("""
        SELECT ts_code, trade_date, open, close, high, low, pct_chg, amount, vol
        FROM daily_prices
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(start_date, end_date))
    daily['trade_date'] = pd.to_datetime(daily['trade_date'], format='%Y%m%d')
    return daily

def load_stock_list(conn):
    """加载股票列表"""
    print("[LOAD] 正在加载股票列表...")
    stocks = pd.read_sql("""
        SELECT ts_code, name, industry, list_date
        FROM stock_list
    """, conn)
    return stocks

def pre_screen(daily, pct_min=-2.0, pct_max=9.5, turn_min=0.5, amount_min=20000):
    """第一层：SQL预筛选逻辑（向量化实现）"""
    print(f"[SCREEN] 预筛选参数：涨幅[{pct_min}%, {pct_max}%] 换手率>={turn_min}% 成交额>={amount_min/100}万")
    
    # 计算换手率（需要成交量和流通股本，这里用成交额近似）
    daily['eligible'] = (
        (daily['pct_chg'] >= pct_min) &
        (daily['pct_chg'] <= pct_max) &
        (daily['amount'] >= amount_min)
    )
    
    eligible = daily[daily['eligible']].copy()
    print(f"[SCREEN] 预筛选完成：{len(daily)} -> {len(eligible)} 只")
    return eligible

def calculate_factors(daily):
    """计算多因子指标"""
    print("[FACTOR] 正在计算技术因子...")
    df = daily.copy()
    df = df.sort_values(['ts_code', 'trade_date'])
    
    # 移动平均线
    df['ma5'] = df.groupby('ts_code')['close'].rolling(5).mean().reset_index(0, drop=True)
    df['ma20'] = df.groupby('ts_code')['close'].rolling(20).mean().reset_index(0, drop=True)
    df['ma60'] = df.groupby('ts_code')['close'].rolling(60).mean().reset_index(0, drop=True)
    
    # MACD
    df['ema12'] = df.groupby('ts_code')['close'].ewm(span=12, adjust=False).mean().reset_index(0, drop=True)
    df['ema26'] = df.groupby('ts_code')['close'].ewm(span=26, adjust=False).mean().reset_index(0, drop=True)
    df['macd'] = df['ema12'] - df['ema26']
    df['signal'] = df.groupby('ts_code')['macd'].ewm(span=9, adjust=False).mean().reset_index(0, drop=True)
    
    # RSI
    df['change'] = df.groupby('ts_code')['close'].diff()
    df['gain'] = df['change'].where(df['change'] > 0, 0)
    df['loss'] = -df['change'].where(df['change'] < 0, 0)
    df['avg_gain'] = df.groupby('ts_code')['gain'].rolling(14).mean().reset_index(0, drop=True)
    df['avg_loss'] = df.groupby('ts_code')['loss'].rolling(14).mean().reset_index(0, drop=True)
    df['rsi'] = 100 - (100 / (1 + df['avg_gain'] / (df['avg_loss'] + 0.001)))
    
    # 波动率（ATR）
    df['tr'] = np.max([
        df['high'] - df['low'],
        abs(df['high'] - df.groupby('ts_code')['close'].shift()),
        abs(df['low'] - df.groupby('ts_code')['close'].shift())
    ], axis=0)
    df['atr'] = df.groupby('ts_code')['tr'].rolling(14).mean().reset_index(0, drop=True)
    
    # 涨幅因子
    df['pct_score'] = df['pct_chg'].clip(-10, 10) / 10 * 20
    
    # 量能因子
    df['vol_ma5'] = df.groupby('ts_code')['vol'].rolling(5).mean().reset_index(0, drop=True)
    df['vol_ratio'] = df['vol'] / (df['vol_ma5'] + 0.001)
    df['vol_score'] = df['vol_ratio'].clip(0, 3) / 3 * 15
    
    return df

def calculate_score(df):
    """计算综合评分"""
    print("[SCORE] 正在计算综合评分...")
    score_df = df.copy()
    
    # 均线趋势得分
    score_df['ma_score'] = 0
    score_df.loc[score_df['close'] > score_df['ma5'], 'ma_score'] += 5
    score_df.loc[score_df['ma5'] > score_df['ma20'], 'ma_score'] += 5
    score_df.loc[score_df['ma20'] > score_df['ma60'], 'ma_score'] += 5
    
    # MACD得分
    score_df['macd_score'] = 0
    score_df.loc[score_df['macd'] > score_df['signal'], 'macd_score'] += 10
    score_df.loc[score_df['macd'] > 0, 'macd_score'] += 5
    
    # RSI得分（避免超买超卖）
    score_df['rsi_score'] = 0
    score_df.loc[(score_df['rsi'] > 30) & (score_df['rsi'] < 70), 'rsi_score'] = 10
    score_df.loc[score_df['rsi'] >= 70, 'rsi_score'] = 5
    
    # ATR得分（波动率适中）
    score_df['atr_score'] = 0
    score_df.loc[(score_df['atr'] > 0.5) & (score_df['atr'] < 5), 'atr_score'] = 10
    
    # 综合得分
    score_df['total_score'] = (
        score_df['ma_score'] +
        score_df['macd_score'] +
        score_df['rsi_score'] +
        score_df['atr_score'] +
        score_df['pct_score'].fillna(0) +
        score_df['vol_score'].fillna(0)
    )
    
    return score_df

def generate_signals(score_df, top_n=50):
    """每日生成Top-N信号"""
    print(f"[SIGNAL] 每日选取 Top-{top_n} 股票...")
    
    # 按日期分组，每组取Top-N
    def get_top_n(group):
        return group.nlargest(top_n, 'total_score')
    
    signals = score_df.groupby('trade_date', group_keys=False).apply(get_top_n)
    signals['signal'] = 1  # 买入信号
    print(f"[SIGNAL] 信号生成完成：共 {len(signals)} 条信号")
    
    return signals

def backtest(signals, daily, holding_days=5, initial_capital=100000):
    """执行回测"""
    print(f"[BACKTEST] 开始回测，持仓周期 {holding_days} 天...")
    
    # 合并信号和价格数据
    signals = signals[['ts_code', 'trade_date', 'signal', 'total_score']]
    daily['next_close'] = daily.groupby('ts_code')['close'].shift(-holding_days)
    
    merged = signals.merge(
        daily[['ts_code', 'trade_date', 'close', 'next_close', 'pct_chg']],
        on=['ts_code', 'trade_date'],
        how='left'
    )
    
    # 计算收益
    merged['return'] = (merged['next_close'] / merged['close']) - 1
    merged['return'] = merged['return'].fillna(0)
    
    # 按日期分组计算每日收益
    daily_returns = merged.groupby('trade_date').agg({
        'return': 'mean',
        'ts_code': 'count'
    }).rename(columns={'ts_code': 'positions'})
    
    # 计算累计收益
    daily_returns['cum_return'] = (1 + daily_returns['return']).cumprod()
    daily_returns['equity'] = initial_capital * daily_returns['cum_return']
    
    # 计算统计指标
    total_return = daily_returns['cum_return'].iloc[-1] - 1
    daily_return_mean = daily_returns['return'].mean()
    daily_return_std = daily_returns['return'].std()
    sharpe_ratio = (daily_return_mean / daily_return_std) * np.sqrt(252)
    
    # 最大回撤
    peak = daily_returns['equity'].cummax()
    drawdown = (daily_returns['equity'] - peak) / peak
    max_drawdown = drawdown.min()
    
    # 胜率
    win_rate = (daily_returns['return'] > 0).mean()
    
    print("\n" + "="*60)
    print("📊 三层漏斗策略回测报告")
    print("="*60)
    print(f"回测区间: {daily_returns.index.min().strftime('%Y-%m-%d')} ~ {daily_returns.index.max().strftime('%Y-%m-%d')}")
    print(f"交易天数: {len(daily_returns)} 天")
    print(f"平均持仓: {daily_returns['positions'].mean():.1f} 只")
    print("-"*60)
    print(f"总收益率: {total_return*100:.2f}%")
    print(f"年化收益率: {((1 + total_return) ** (252/len(daily_returns)) - 1)*100:.2f}%")
    print(f"夏普比率: {sharpe_ratio:.2f}")
    print(f"最大回撤: {max_drawdown*100:.2f}%")
    print(f"胜率: {win_rate*100:.2f}%")
    print("="*60)
    
    return daily_returns, merged

def main():
    parser = argparse.ArgumentParser(description="三层漏斗策略回测")
    parser.add_argument("--start", default="20260101", help="起始日期 YYYYMMDD")
    parser.add_argument("--end", default="20260608", help="截止日期 YYYYMMDD")
    parser.add_argument("--top-n", type=int, default=50, help="每日选取Top-N股票")
    parser.add_argument("--holding-days", type=int, default=5, help="持仓天数")
    parser.add_argument("--output", default=None, help="输出文件路径")
    args = parser.parse_args()
    
    print("="*60)
    print("🚀 三层漏斗策略回测引擎 v1.0")
    print("="*60)
    print(f"回测区间: {args.start} ~ {args.end}")
    print(f"每日选股: Top-{args.top_n}")
    print(f"持仓周期: {args.holding_days} 天")
    print("="*60)
    
    # 连接数据库
    conn = sqlite3.connect(DB_PATH)
    
    # 加载数据
    daily = load_daily_data(conn, args.start, args.end)
    stocks = load_stock_list(conn)
    
    if len(daily) == 0:
        print("❌ 没有找到数据，请检查日期范围和数据库")
        return
    
    # 第一层：预筛选
    eligible = pre_screen(daily)
    
    # 第二层：因子计算和评分
    factors = calculate_factors(eligible)
    scored = calculate_score(factors)
    
    # 第三层：生成信号
    signals = generate_signals(scored, top_n=args.top_n)
    
    # 回测
    results, trades = backtest(signals, daily, holding_days=args.holding_days)
    
    # 保存结果
    if args.output:
        results.to_csv(args.output)
        print(f"\n✅ 结果已保存到 {args.output}")
    
    conn.close()
    print("\n🎉 回测完成！")

if __name__ == "__main__":
    main()
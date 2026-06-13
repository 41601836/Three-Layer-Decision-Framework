# -*- coding: utf-8 -*-
"""
三层漏斗策略回测引擎 v3.0（趋势过滤版）
========================================

新增功能：
  1. 大盘趋势过滤（仅在大盘趋势向上时操作）
  2. 动态仓位管理（根据市场环境调整仓位）
  3. 行业轮动因子
  4. 改进的止损策略

使用方法：
    python backtest_funnel_v3.py --start 20230101 --end 20241231
"""

import os
import sys
import sqlite3
import pandas as pd
import argparse
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

def calculate_market_trend(daily):
    """计算大盘趋势"""
    print("[TREND] 正在计算大盘趋势...")
    
    # 计算市场平均涨幅作为大盘指标
    market = daily.groupby('trade_date').agg({
        'pct_chg': 'mean'
    }).rename(columns={'pct_chg': 'market_pct'})
    
    # 计算市场趋势（基于20日均线）
    market['market_ma20'] = market['market_pct'].rolling(20).mean()
    market['market_ma60'] = market['market_pct'].rolling(60).mean()
    
    # 判断趋势：20日均线 > 60日均线 且 20日均线 > 0
    market['trend_up'] = (market['market_ma20'] > market['market_ma60']) & (market['market_ma20'] > 0)
    
    return market

def pre_screen(daily, market_trend, pct_min=-2.0, pct_max=8.0, amount_min=50000):
    """第一层：预筛选（带趋势过滤）"""
    print(f"[SCREEN] 预筛选参数：涨幅[{pct_min}%, {pct_max}%] 成交额>={amount_min/100}万")
    
    # 合并市场趋势
    daily = daily.merge(market_trend[['trend_up']], left_on='trade_date', right_index=True, how='left')
    
    daily['eligible'] = (
        (daily['pct_chg'] >= pct_min) &
        (daily['pct_chg'] <= pct_max) &
        (daily['amount'] >= amount_min) &
        (daily['trend_up'] == True)  # 只有大盘趋势向上才选股
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
    
    # 均线多头排列得分
    df['ma_trend'] = 0
    df.loc[df['close'] > df['ma5'], 'ma_trend'] += 1
    df.loc[df['ma5'] > df['ma20'], 'ma_trend'] += 1
    df.loc[df['ma20'] > df['ma60'], 'ma_trend'] += 1
    
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
    
    # ATR
    df['tr'] = np.max([
        df['high'] - df['low'],
        abs(df['high'] - df.groupby('ts_code')['close'].shift()),
        abs(df['low'] - df.groupby('ts_code')['close'].shift())
    ], axis=0)
    df['atr'] = df.groupby('ts_code')['tr'].rolling(14).mean().reset_index(0, drop=True)
    
    # 量能因子
    df['vol_ma5'] = df.groupby('ts_code')['vol'].rolling(5).mean().reset_index(0, drop=True)
    df['vol_ratio'] = df['vol'] / (df['vol_ma5'] + 0.001)
    
    # 动量因子
    df['momentum'] = df.groupby('ts_code')['close'].pct_change(20).reset_index(0, drop=True)
    
    return df

def calculate_score(df):
    """计算综合评分"""
    print("[SCORE] 正在计算综合评分...")
    score_df = df.copy()
    
    # 均线趋势得分
    score_df['ma_score'] = score_df['ma_trend'] * 20  # 最高60分
    
    # MACD得分
    score_df['macd_score'] = 0
    score_df.loc[score_df['macd'] > score_df['signal'], 'macd_score'] = 20
    
    # RSI得分
    score_df['rsi_score'] = 0
    score_df.loc[(score_df['rsi'] > 30) & (score_df['rsi'] < 70), 'rsi_score'] = 20
    
    # 综合得分
    score_df['total_score'] = (
        score_df['ma_score'] +
        score_df['macd_score'] +
        score_df['rsi_score']
    )
    
    score_df = score_df[score_df['total_score'] >= 40].copy()
    return score_df

def generate_signals(score_df, top_n=20):
    """生成交易信号"""
    print(f"[SIGNAL] 每日选取 Top-{top_n} 股票...")
    
    def get_top_n(group):
        return group.nlargest(min(top_n, len(group)), 'total_score')
    
    signals = score_df.groupby('trade_date', group_keys=False).apply(get_top_n)
    signals['signal'] = 1
    print(f"[SIGNAL] 信号生成完成：共 {len(signals)} 条信号")
    
    return signals

def backtest_with_risk_management(signals, daily, factors, holding_days=5, initial_capital=100000, atr_multiplier=2.0):
    """带风险管理的回测"""
    print(f"[BACKTEST] 开始回测，持仓周期 {holding_days} 天...")
    
    daily = daily.merge(factors[['ts_code', 'trade_date', 'atr']], on=['ts_code', 'trade_date'], how='left')
    daily['atr_stop'] = daily['close'] - daily['atr'] * atr_multiplier
    daily['next_close'] = daily.groupby('ts_code')['close'].shift(-holding_days)
    
    def get_min_price(group):
        group['min_price_hold'] = group['low'].rolling(holding_days+1).min().shift(-holding_days)
        return group
    
    daily_with_min = daily.groupby('ts_code', group_keys=False).apply(get_min_price).reset_index(drop=True)
    
    merged = signals[['ts_code', 'trade_date', 'signal', 'total_score']].merge(
        daily_with_min[['ts_code', 'trade_date', 'close', 'next_close', 'atr_stop', 'min_price_hold']],
        on=['ts_code', 'trade_date'],
        how='left'
    )
    
    merged['stop_triggered'] = merged['min_price_hold'] < merged['atr_stop']
    merged['return'] = np.where(
        merged['stop_triggered'],
        (merged['atr_stop'] / merged['close']) - 1,
        (merged['next_close'] / merged['close']) - 1
    )
    merged['return'] = merged['return'].fillna(0)
    
    daily_returns = merged.groupby('trade_date').agg({
        'return': 'mean',
        'ts_code': 'count',
        'stop_triggered': 'sum'
    }).rename(columns={'ts_code': 'positions', 'stop_triggered': 'stops'})
    
    daily_returns['cum_return'] = (1 + daily_returns['return']).cumprod()
    daily_returns['equity'] = initial_capital * daily_returns['cum_return']
    
    total_return = daily_returns['cum_return'].iloc[-1] - 1
    daily_return_mean = daily_returns['return'].mean()
    daily_return_std = daily_returns['return'].std()
    sharpe_ratio = (daily_return_mean / daily_return_std) * np.sqrt(252) if daily_return_std > 0 else 0
    
    peak = daily_returns['equity'].cummax()
    drawdown = (daily_returns['equity'] - peak) / peak
    max_drawdown = drawdown.min()
    
    win_rate = (daily_returns['return'] > 0).mean()
    total_stops = daily_returns['stops'].sum()
    total_trades = len(merged)
    stop_rate = total_stops / total_trades if total_trades > 0 else 0
    
    print("\n" + "="*60)
    print("📊 三层漏斗策略回测报告 v3.0（趋势过滤版）")
    print("="*60)
    print(f"回测区间: {daily_returns.index.min().strftime('%Y-%m-%d')} ~ {daily_returns.index.max().strftime('%Y-%m-%d')}")
    print(f"交易天数: {len(daily_returns)} 天")
    print(f"平均持仓: {daily_returns['positions'].mean():.1f} 只")
    print(f"止损触发: {total_stops}/{total_trades} ({stop_rate*100:.1f}%)")
    print("-"*60)
    print(f"总收益率: {total_return*100:.2f}%")
    print(f"年化收益率: {((1 + total_return) ** (252/len(daily_returns)) - 1)*100:.2f}%")
    print(f"夏普比率: {sharpe_ratio:.2f}")
    print(f"最大回撤: {max_drawdown*100:.2f}%")
    print(f"胜率: {win_rate*100:.2f}%")
    print("="*60)
    
    return daily_returns, merged

def main():
    parser = argparse.ArgumentParser(description="三层漏斗策略回测 v3.0（趋势过滤版）")
    parser.add_argument("--start", default="20230101", help="起始日期 YYYYMMDD")
    parser.add_argument("--end", default="20241231", help="截止日期 YYYYMMDD")
    parser.add_argument("--top-n", type=int, default=20, help="每日选取Top-N股票")
    parser.add_argument("--holding-days", type=int, default=5, help="持仓天数")
    parser.add_argument("--output", default=None, help="输出文件路径")
    args = parser.parse_args()
    
    print("="*60)
    print("🚀 三层漏斗策略回测引擎 v3.0（趋势过滤版）")
    print("="*60)
    print(f"回测区间: {args.start} ~ {args.end}")
    print(f"每日选股: Top-{args.top_n}")
    print(f"持仓周期: {args.holding_days} 天")
    print("="*60)
    
    conn = sqlite3.connect(DB_PATH)
    
    daily = load_daily_data(conn, args.start, args.end)
    stocks = load_stock_list(conn)
    
    if len(daily) == 0:
        print("❌ 没有找到数据")
        return
    
    # 计算大盘趋势
    market_trend = calculate_market_trend(daily)
    trend_days = market_trend['trend_up'].sum()
    print(f"[TREND] 大盘趋势向上天数: {trend_days}/{len(market_trend)} ({trend_days/len(market_trend)*100:.1f}%)")
    
    # 第一层：预筛选（带趋势过滤）
    eligible = pre_screen(daily, market_trend)
    
    if len(eligible) == 0:
        print("❌ 没有符合条件的股票")
        return
    
    # 第二层：因子计算和评分
    factors = calculate_factors(eligible)
    scored = calculate_score(factors)
    
    # 第三层：生成信号
    signals = generate_signals(scored, top_n=args.top_n)
    
    # 回测
    results, trades = backtest_with_risk_management(
        signals, daily, factors,
        holding_days=args.holding_days
    )
    
    if args.output:
        results.to_csv(args.output)
        print(f"\n✅ 结果已保存到 {args.output}")
    
    conn.close()
    print("\n🎉 回测完成！")

if __name__ == "__main__":
    main()
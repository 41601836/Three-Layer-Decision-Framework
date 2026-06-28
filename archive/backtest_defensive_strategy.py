# -*- coding: utf-8 -*-
"""
防御型三因子策略回测（红利+低波+质量）
================================================
适用于弱势市场的防御型策略：
  1. 红利因子（40%权重）：高股息率提供稳定现金流
  2. 低波因子（30%权重）：低波动率降低回撤风险
  3. 质量因子（30%权重）：高ROE确保企业盈利能力
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
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")
sys.path.insert(0, ROOT_DIR)

STOP_LOSS_PCT = 0.08
HOLD_DAYS = 20

def calculate_defensive_score(row):
    """
    计算防御型复合评分（0-100）
    权重分配：低波(50%) + 动量(30%) + 估值(20%)
    使用纯量价因子，不依赖财务数据
    """
    score = 0
    
    # 低波因子（50%权重）
    volatility_60d = row.get('volatility_60d', 1.0)
    if volatility_60d < 0.15:
        score += 50
    elif volatility_60d < 0.20:
        score += 40
    elif volatility_60d < 0.25:
        score += 30
    elif volatility_60d < 0.35:
        score += 15
    
    # 动量因子（30%权重）- 弱势市场中选择相对抗跌的股票
    ret_20d = row.get('ret_20d', 0)
    if ret_20d > 0:
        score += 30
    elif ret_20d > -0.05:
        score += 20
    elif ret_20d > -0.10:
        score += 10
    
    # 估值因子（20%权重）- 使用PB作为价值指标
    pb = row.get('pb', 100)
    if pb > 0 and pb < 2:
        score += 20
    elif pb > 0 and pb < 3:
        score += 10
    
    return score

def load_data(conn, start_date, end_date):
    pre_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")
    
    print("[LOAD] 日线数据...")
    daily = pd.read_sql("""
        SELECT ts_code, trade_date, close, pct_chg, vol, amount
        FROM daily_prices
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(pre_start, end_date))
    
    print("[LOAD] 股票列表与行业...")
    stock_list = pd.read_sql("SELECT ts_code, industry FROM stock_list", conn)
    
    print("[LOAD] 估值数据...")
    basic = pd.read_sql("""
        SELECT ts_code, trade_date, circ_mv, pe, pb
        FROM daily_basic
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(start_date, end_date))
    basic['trade_date'] = pd.to_datetime(basic['trade_date'])
    
    return daily, stock_list, basic

def compute_signals(daily, stock_list, basic, start_date):
    daily = daily.merge(stock_list, on='ts_code', how='left')
    
    daily['trade_date'] = pd.to_datetime(daily['trade_date'])
    daily = daily.sort_values(['ts_code', 'trade_date'])
    
    print("[FACTOR] 计算60日波动率...")
    daily['volatility_60d'] = daily.groupby('ts_code')['pct_chg'].transform(
        lambda x: x.rolling(60).std() / 100
    )
    
    print("[FACTOR] 计算20日收益率...")
    daily['ret_20d'] = daily.groupby('ts_code')['close'].transform(lambda x: x.pct_change(20))
    
    print("[FACTOR] 合并估值数据...")
    daily = daily.merge(basic, on=['ts_code', 'trade_date'], how='left')
    daily['circ_mv_yi'] = daily['circ_mv'] / 100000000  # 转换为亿元
    daily['circ_mv_yi'] = daily.groupby('ts_code')['circ_mv_yi'].ffill().bfill().fillna(0)
    daily['pb'] = daily.groupby('ts_code')['pb'].ffill().bfill().fillna(100)
    
    df = daily[daily['trade_date'] >= start_date].copy()
    
    print("[FACTOR] 计算防御型复合评分...")
    df['defensive_score'] = df.apply(calculate_defensive_score, axis=1)
    
    print("[SIGNAL] 生成信号...")
    df['signal'] = (df['defensive_score'] >= 40) & \
                   (df['pb'] > 0) & (df['pb'] < 5) & \
                   (df['volatility_60d'] < 0.35) & \
                   (df['circ_mv_yi'] >= 50.0)
    
    n_signals = df['signal'].sum()
    print(f"[SIGNAL] 防御型策略信号数: {n_signals:,} 条")
    
    return df

def run_backtest(daily_all, signals_df, start_date, end_date):
    trade_dates = sorted(signals_df['trade_date'].dt.strftime('%Y%m%d').unique())
    signals_by_date = {}
    
    for dt, grp in signals_df[signals_df['signal']].groupby('trade_date'):
        top_stocks = grp.nlargest(20, 'defensive_score')
        signals_by_date[dt.strftime('%Y%m%d')] = top_stocks['ts_code'].tolist()
    
    price_by_code = {}
    for code, grp in daily_all.groupby('ts_code'):
        price_by_code[code] = grp
    
    initial_capital = 1000000.0
    cash = initial_capital
    active_positions = {}
    trade_history = []
    portfolio_equity = []
    
    for date in trade_dates:
        today_signals = signals_by_date.get(date, [])
        
        closed_codes = []
        for code, pos in list(active_positions.items()):
            df_p = price_by_code.get(code)
            if df_p is None:
                continue
            
            pos['hold_days'] += 1
            today_row = df_p[df_p['trade_date'] == date]
            if not today_row.empty:
                pos['current_price'] = float(today_row.iloc[0]['close'])
                day_close = float(today_row.iloc[0]['close'])
                
                if day_close <= pos['stop_price']:
                    exit_price = pos['stop_price']
                    cash += exit_price * pos['shares']
                    trade_history.append({
                        "ts_code": code,
                        "entry_date": pos["entry_date"],
                        "exit_date": date,
                        "entry_price": pos["entry_price"],
                        "exit_price": exit_price,
                        "shares": pos["shares"],
                        "exit_pct": (exit_price - pos["entry_price"]) / pos["entry_price"] * 100,
                        "reason": "止损",
                        "hold_days": pos["hold_days"]
                    })
                    closed_codes.append(code)
                    continue
                
                if pos['hold_days'] >= HOLD_DAYS:
                    exit_price = day_close
                    cash += exit_price * pos['shares']
                    trade_history.append({
                        "ts_code": code,
                        "entry_date": pos["entry_date"],
                        "exit_date": date,
                        "entry_price": pos["entry_price"],
                        "exit_price": exit_price,
                        "shares": pos["shares"],
                        "exit_pct": (exit_price - pos["entry_price"]) / pos["entry_price"] * 100,
                        "reason": "持有期满",
                        "hold_days": pos["hold_days"]
                    })
                    closed_codes.append(code)
                    continue
        
        for code in closed_codes:
            if code in active_positions:
                del active_positions[code]
        
        portfolio_value = sum(pos["current_price"] * pos["shares"] for pos in active_positions.values())
        equity = cash + portfolio_value
        portfolio_equity.append({"date": date, "equity": equity})
        
        max_pos = min(0.8, 0.15 * (20 - len(active_positions)))
        if max_pos <= 0:
            continue
        
        for code in today_signals:
            if code in active_positions:
                continue
            
            df_p = price_by_code.get(code)
            if df_p is None:
                continue
            
            today_row = df_p[df_p['trade_date'] == date]
            if today_row.empty:
                continue
            
            close_price = float(today_row.iloc[0]['close'])
            min20 = df_p[df_p['trade_date'] <= date]['close'].tail(20).min()
            
            buy_value = equity * 0.05
            shares = int(buy_value / close_price)
            
            if shares > 0 and cash >= shares * close_price:
                cash -= shares * close_price
                stop_fixed = close_price * (1 - STOP_LOSS_PCT)
                stop_struct = (min20 * 0.98) if min20 else stop_fixed
                stop_price = max(stop_fixed, stop_struct)
                
                active_positions[code] = {
                    "entry_date": date,
                    "entry_price": close_price,
                    "stop_price": stop_price,
                    "shares": shares,
                    "current_price": close_price,
                    "hold_days": 0
                }
    
    df_trades = pd.DataFrame(trade_history)
    df_equity = pd.DataFrame(portfolio_equity)
    
    return df_equity, df_trades

def analyze_performance(df_equity, df_trades):
    if not df_trades.empty:
        win_rate = (df_trades["exit_pct"] > 0).mean()
        avg_ret = df_trades["exit_pct"].mean()
        gains = df_trades.loc[df_trades["exit_pct"] > 0, "exit_pct"].mean()
        losses = df_trades.loc[df_trades["exit_pct"] < 0, "exit_pct"].mean()
        pl_ratio = abs(gains / losses) if losses != 0 else float("nan")
        max_loss = df_trades["exit_pct"].min()
        total_trades = len(df_trades)
    else:
        win_rate = avg_ret = gains = losses = pl_ratio = max_loss = 0.0
        total_trades = 0
    
    if not df_equity.empty:
        initial_cap = df_equity["equity"].iloc[0]
        final_cap = df_equity["equity"].iloc[-1]
        total_ret = (final_cap - initial_cap) / initial_cap
        days = (pd.to_datetime(df_equity["date"].iloc[-1]) - pd.to_datetime(df_equity["date"].iloc[0])).days
        ann_ret = (1 + total_ret) ** (365 / days) - 1 if days > 0 else 0
        
        df_equity["drawdown"] = 1 - df_equity["equity"] / df_equity["equity"].cummax()
        max_dd = df_equity["drawdown"].max()
        
        daily_returns = df_equity["equity"].pct_change().dropna()
        sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252) if daily_returns.std() > 0 else 0
    else:
        initial_cap = final_cap = 1000000.0
        total_ret = ann_ret = max_dd = sharpe = 0.0
    
    print(f"\n{'='*70}")
    print("       StockAI v4.0 - 防御型三因子策略回测报告")
    print(f"{'='*70}")
    print(f"\n【单信号裸交易绩效】")
    print(f"  交易总笔数  : {total_trades:,} 笔")
    print(f"  信号平均胜率: {win_rate:.1%}")
    print(f"  单次均收益  : {avg_ret:+.2f}%")
    print(f"  平均盈利    : {gains:+.2f}%")
    print(f"  平均亏损    : {losses:+.2f}%")
    print(f"  系统盈亏比  : {pl_ratio:.2f}")
    print(f"  最大单笔亏损: {max_loss:+.2f}%")
    
    print(f"\n【限仓组合绩效】")
    print(f"  期末总资产  : ¥{final_cap:,.2f} (初始 ¥{initial_cap:,.2f})")
    print(f"  组合总收益率: {total_ret:+.2%}")
    print(f"  组合年化收益: {ann_ret:+.2%}")
    print(f"  组合最大回撤: {max_dd:.2%}")
    print(f"  组合夏普比率: {sharpe:.3f}")

def main():
    parser = argparse.ArgumentParser(description="防御型三因子策略回测（红利+低波+质量）")
    parser.add_argument("--start", default="20260101")
    parser.add_argument("--end", default="20260625")
    args = parser.parse_args()
    
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    
    try:
        daily, stock_list, basic = load_data(conn, args.start, args.end)
        signals_df = compute_signals(daily, stock_list, basic, args.start)
        df_equity, df_trades = run_backtest(daily, signals_df, args.start, args.end)
        analyze_performance(df_equity, df_trades)
    finally:
        conn.close()
    
    print(f"\n[DONE] 防御型策略回测完成，耗时: {time.time() - t0:.2f} 秒")

if __name__ == "__main__":
    main()
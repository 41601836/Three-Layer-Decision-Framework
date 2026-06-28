# -*- coding: utf-8 -*-
"""
简化版综合评分策略 - 最小过滤，专注评分
"""
import sys
import os
import json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

INITIAL_WEIGHTS = {
    'pb': 0.61,
    'ret_20d': 1.48,
    'circ_mv_yi': 0.61,
    'winner_rate': 0.97
}

def calc_composite_score(df, weights):
    """加权综合评分"""
    score = pd.Series(0.0, index=df.index)
    valid_factors = []
    
    for factor, weight in weights.items():
        if factor not in df.columns:
            continue
        
        factor_values = df[factor].copy()
        
        if factor_values.isna().all():
            continue
        
        valid_factors.append(factor)
        
        if factor_values.min() == factor_values.max():
            normalized = pd.Series(0.5, index=df.index)
        else:
            normalized = (factor_values - factor_values.min()) / (factor_values.max() - factor_values.min())
        
        if factor in ['pb']:
            normalized = 1 - normalized
        
        score += normalized * weight
    
    if valid_factors:
        score = score / len(valid_factors)
    
    return score

def backtest_with_top_n(executor, weights, top_n=10):
    """使用Top-N选股进行回测"""
    daily = executor.load_data()
    df = daily[daily['trade_date'] >= executor.start_date].copy()
    
    df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
    
    df['composite_score'] = np.nan
    df['signal'] = False
    
    total_days = len(df['trade_date'].unique())
    print(f"总交易日: {total_days}")
    
    for date in sorted(df['trade_date'].unique()):
        day_mask = df['trade_date'] == date
        day_df = df[day_mask].copy()
        
        if len(day_df) > top_n:
            day_df['_score'] = calc_composite_score(day_df, weights)
            
            selected = day_df.sort_values('_score', ascending=False).head(top_n)
            df.loc[selected.index, 'signal'] = True
    
    signal_count = df['signal'].sum()
    signal_per_day = signal_count / total_days if total_days > 0 else 0
    
    print(f"总信号数: {signal_count}, 日均: {signal_per_day:.1f}")
    
    trades = []
    positions = {}
    
    for date in sorted(df['trade_date'].unique()):
        day_df = df[df['trade_date'] == date]
        signals = day_df[day_df['signal']]['ts_code'].tolist()
        
        closed = []
        for code, pos in list(positions.items()):
            pos['days'] += 1
            if pos['days'] >= 10:
                code_df = day_df[day_df['ts_code'] == code]
                exit_price = float(code_df['close'].iloc[0]) if not code_df.empty else pos['entry_price']
                trades.append({
                    'entry_date': pos['entry_date'],
                    'exit_date': date,
                    'entry_price': pos['entry_price'],
                    'exit_price': exit_price,
                    'return': (exit_price - pos['entry_price']) / pos['entry_price']
                })
                closed.append(code)
        
        for code in closed:
            del positions[code]
        
        for code in signals:
            if code not in positions:
                entry_price = float(day_df[day_df['ts_code'] == code]['close'].iloc[0])
                positions[code] = {
                    'entry_date': date,
                    'entry_price': entry_price,
                    'days': 0
                }
    
    if trades:
        trade_df = pd.DataFrame(trades)
        win_rate = (trade_df['return'] > 0).mean()
        total_return = (1 + trade_df['return']).prod() - 1
        equity = (1 + trade_df['return']).cumprod()
        max_drawdown = (1 - equity / equity.cummax()).max()
        trade_count = len(trades)
    else:
        win_rate = 0.0
        total_return = 0.0
        max_drawdown = 0.0
        trade_count = 0
    
    return {
        'win_rate': win_rate,
        'total_return': total_return,
        'max_drawdown': max_drawdown,
        'signal_count': signal_count,
        'signal_per_day': signal_per_day,
        'trade_count': trade_count,
        'top_n': top_n,
        'weights': weights
    }

def main():
    executor = BacktestExecutor("20260101", "20260625")
    
    print("="*70)
    print("          简化版综合评分策略")
    print("="*70)
    print(f"初始权重: {INITIAL_WEIGHTS}")
    print("="*70)
    
    results = []
    
    top_n_values = [5, 8, 10, 12, 15]
    
    print(f"\n测试不同每日选股数量 ({top_n_values}):")
    
    for top_n in top_n_values:
        print(f"\n--- Top-{top_n} ---")
        
        result = backtest_with_top_n(executor, INITIAL_WEIGHTS, top_n)
        
        print(f"胜率: {result['win_rate']:.1%}")
        print(f"总收益: {result['total_return']:.1%}")
        print(f"最大回撤: {result['max_drawdown']:.1%}")
        print(f"信号数: {result['signal_count']} ({result['signal_per_day']:.1f}/天)")
        print(f"交易数: {result['trade_count']}")
        
        meets_constraints = (
            result['win_rate'] >= 0.55 and result['win_rate'] <= 0.65 and
            result['signal_per_day'] >= 5 and result['signal_per_day'] <= 10 and
            result['max_drawdown'] <= 0.20 and
            result['trade_count'] >= 10
        )
        
        if meets_constraints:
            print("✅ 满足所有约束！")
        
        results.append(result)
    
    print(f"\n\n{'='*70}")
    print("          结果汇总")
    print("="*70)
    
    for r in results:
        status = "✅" if (
            r['win_rate'] >= 0.55 and r['win_rate'] <= 0.65 and
            r['signal_per_day'] >= 5 and r['signal_per_day'] <= 10 and
            r['max_drawdown'] <= 0.20 and
            r['trade_count'] >= 10
        ) else "❌"
        
        print(f"Top-{r['top_n']}: 胜率={r['win_rate']:.1%}, 信号={r['signal_per_day']:.1f}/天, 回撤={r['max_drawdown']:.1%}, 收益={r['total_return']:.1%} {status}")

if __name__ == "__main__":
    main()
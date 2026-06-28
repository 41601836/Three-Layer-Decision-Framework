# -*- coding: utf-8 -*-
"""
阈值过滤策略 - 使用原始逻辑，调整参数
"""
import sys
import os
import json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

def evaluate_threshold_strategy(df, weights, thresholds, factor_directions):
    """评估阈值策略"""
    score = pd.Series(0.0, index=df.index)
    
    for factor, weight in weights.items():
        if factor not in df.columns:
            continue
        
        factor_values = df[factor].copy()
        threshold = thresholds.get(factor, 0.5)
        direction = factor_directions.get(factor, 'higher_better')
        
        if direction == 'higher_better':
            mask = factor_values >= threshold
        else:
            mask = factor_values <= threshold
        
        score += mask.astype(float) * weight
    
    return score

def backtest_threshold_strategy(executor, weights, thresholds, factor_directions, top_n=10):
    """回测阈值策略"""
    daily = executor.load_data()
    df = daily[daily['trade_date'] >= executor.start_date].copy()
    
    df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
    
    df['signal'] = False
    
    total_weight = sum(weights.values())
    min_score_ratio = 0.6
    
    for date in sorted(df['trade_date'].unique()):
        day_mask = df['trade_date'] == date
        day_df = df[day_mask].copy()
        
        if len(day_df) > top_n:
            day_df['_score'] = evaluate_threshold_strategy(day_df, weights, thresholds, factor_directions)
            
            min_score = total_weight * min_score_ratio
            qualified = day_df[day_df['_score'] >= min_score]
            
            if len(qualified) > top_n:
                selected = qualified.sort_values('_score', ascending=False).head(top_n)
                df.loc[selected.index, 'signal'] = True
            elif len(qualified) > 0:
                df.loc[qualified.index, 'signal'] = True
    
    signal_count = df['signal'].sum()
    total_days = len(df['trade_date'].unique())
    signal_per_day = signal_count / total_days if total_days > 0 else 0
    
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
        'weights': weights,
        'thresholds': thresholds
    }

def main():
    executor = BacktestExecutor("20260101", "20260625")
    
    print("="*70)
    print("          阈值过滤策略优化")
    print("="*70)
    print("="*70)
    
    factor_directions = {
        'pb': 'lower_better',
        'ret_20d': 'higher_better',
        'circ_mv_yi': 'higher_better',
        'winner_rate': 'higher_better'
    }
    
    param_combinations = [
        {
            'weights': {'pb': 1.0, 'ret_20d': 1.5, 'circ_mv_yi': 1.0, 'winner_rate': 1.0},
            'thresholds': {'pb': 0.70, 'ret_20d': 0.55, 'circ_mv_yi': 0.40, 'winner_rate': 0.30},
            'top_n': 8
        },
        {
            'weights': {'pb': 1.0, 'ret_20d': 2.0, 'circ_mv_yi': 0.8, 'winner_rate': 1.2},
            'thresholds': {'pb': 0.65, 'ret_20d': 0.50, 'circ_mv_yi': 0.35, 'winner_rate': 0.25},
            'top_n': 8
        },
        {
            'weights': {'pb': 0.8, 'ret_20d': 1.8, 'circ_mv_yi': 0.8, 'winner_rate': 1.0},
            'thresholds': {'pb': 0.75, 'ret_20d': 0.60, 'circ_mv_yi': 0.45, 'winner_rate': 0.35},
            'top_n': 6
        },
        {
            'weights': {'pb': 1.2, 'ret_20d': 1.5, 'circ_mv_yi': 1.2, 'winner_rate': 0.8},
            'thresholds': {'pb': 0.60, 'ret_20d': 0.45, 'circ_mv_yi': 0.30, 'winner_rate': 0.20},
            'top_n': 10
        },
        {
            'weights': {'pb': 1.0, 'ret_20d': 1.0, 'circ_mv_yi': 1.0, 'winner_rate': 1.5},
            'thresholds': {'pb': 0.68, 'ret_20d': 0.52, 'circ_mv_yi': 0.38, 'winner_rate': 0.28},
            'top_n': 7
        },
    ]
    
    results = []
    
    for i, params in enumerate(param_combinations, 1):
        print(f"\n--- 组合 {i} ---")
        print(f"权重: {params['weights']}")
        print(f"阈值: {params['thresholds']}")
        print(f"每日选股: Top-{params['top_n']}")
        
        result = backtest_threshold_strategy(executor, params['weights'], params['thresholds'], factor_directions, params['top_n'])
        
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
    
    for i, r in enumerate(results, 1):
        status = "✅" if (
            r['win_rate'] >= 0.55 and r['win_rate'] <= 0.65 and
            r['signal_per_day'] >= 5 and r['signal_per_day'] <= 10 and
            r['max_drawdown'] <= 0.20 and
            r['trade_count'] >= 10
        ) else "❌"
        
        print(f"组合{i}: 胜率={r['win_rate']:.1%}, 信号={r['signal_per_day']:.1f}/天, 回撤={r['max_drawdown']:.1%}, 收益={r['total_return']:.1%} {status}")

if __name__ == "__main__":
    main()
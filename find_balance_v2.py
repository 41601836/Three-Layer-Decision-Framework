# -*- coding: utf-8 -*-
"""
寻找平衡点 v2 - 调整阈值和选股数量
"""
import sys
import os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

def evaluate_threshold_strategy(df, weights, thresholds, factor_directions):
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

def backtest(executor, weights, thresholds, factor_directions, top_n=10, min_score_ratio=0.5):
    daily = executor.load_data()
    df = daily[daily['trade_date'] >= executor.start_date].copy()
    df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
    
    df['signal'] = False
    total_weight = sum(weights.values())
    
    for date in sorted(df['trade_date'].unique()):
        day_mask = df['trade_date'] == date
        day_df = df[day_mask].copy()
        
        if len(day_df) > top_n:
            day_df['_score'] = evaluate_threshold_strategy(day_df, weights, thresholds, factor_directions)
            
            min_score = total_weight * min_score_ratio
            qualified = day_df[day_df['_score'] >= min_score]
            
            if len(qualified) >= top_n:
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
        'min_score_ratio': min_score_ratio
    }

def main():
    executor = BacktestExecutor("20260101", "20260625")
    
    factor_directions = {
        'pb': 'lower_better',
        'ret_20d': 'higher_better',
        'circ_mv_yi': 'higher_better',
        'winner_rate': 'higher_better'
    }
    
    weights = {'pb': 1.0, 'ret_20d': 1.5, 'circ_mv_yi': 1.0, 'winner_rate': 1.0}
    
    print("="*70)
    print("          寻找平衡点 - 调整阈值和最小评分")
    print("="*70)
    
    best_result = None
    best_fitness = -1
    
    for pb_thresh in [0.60, 0.62, 0.65, 0.68, 0.70]:
        for ret20d_thresh in [0.45, 0.50, 0.55, 0.60, 0.65]:
            for mv_thresh in [0.30, 0.35, 0.40, 0.45]:
                for winner_thresh in [0.20, 0.25, 0.30, 0.35]:
                    for top_n in [8, 10, 12]:
                        for min_score_ratio in [0.4, 0.45, 0.5, 0.55]:
                            thresholds = {
                                'pb': pb_thresh,
                                'ret_20d': ret20d_thresh,
                                'circ_mv_yi': mv_thresh,
                                'winner_rate': winner_thresh
                            }
                            
                            result = backtest(executor, weights, thresholds, factor_directions, top_n, min_score_ratio)
                            
                            if result['trade_count'] < 10:
                                continue
                            
                            meets_constraints = (
                                result['win_rate'] >= 0.55 and result['win_rate'] <= 0.65 and
                                result['signal_per_day'] >= 5 and result['signal_per_day'] <= 10 and
                                result['max_drawdown'] <= 0.20
                            )
                            
                            if meets_constraints:
                                fitness = result['total_return'] - result['max_drawdown']
                                
                                if fitness > best_fitness:
                                    best_fitness = fitness
                                    best_result = result
                                    best_result['thresholds'] = thresholds
                                    best_result['weights'] = weights
                                    
                                    print(f"\n✅ 找到满足约束的策略:")
                                    print(f"   阈值: pb={pb_thresh}, ret_20d={ret20d_thresh}, mv={mv_thresh}, winner={winner_thresh}")
                                    print(f"   参数: top_n={top_n}, min_score={min_score_ratio}")
                                    print(f"   胜率: {result['win_rate']:.1%}, 信号: {result['signal_per_day']:.1f}/天, 回撤: {result['max_drawdown']:.1%}, 收益: {result['total_return']:.1%}")
    
    if best_result:
        print(f"\n{'='*70}")
        print("          最优策略")
        print("="*70)
        print(f"\n【策略参数】")
        print(f"  权重: {best_result['weights']}")
        print(f"  阈值: {best_result['thresholds']}")
        print(f"  每日选股: Top-{best_result['top_n']}")
        print(f"  最小评分比例: {best_result['min_score_ratio']}")
        
        print(f"\n【回测绩效】")
        print(f"  胜率: {best_result['win_rate']:.1%}")
        print(f"  总收益: {best_result['total_return']:.1%}")
        print(f"  最大回撤: {best_result['max_drawdown']:.1%}")
        print(f"  信号数: {best_result['signal_per_day']:.1f}/天")
        print(f"  交易数: {best_result['trade_count']}")
    else:
        print("\n❌ 未找到满足所有约束的策略")

if __name__ == "__main__":
    main()
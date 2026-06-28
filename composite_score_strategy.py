# -*- coding: utf-8 -*-
"""
综合评分策略 - 使用加权评分 + 分位选股
目标：胜率55-60%、信号数5-10个/天、回撤<20%
"""
import sys
import os
import json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

# 初始权重（基于v1.1策略）
INITIAL_WEIGHTS = {
    'pb': 0.61,
    'ret_20d': 1.48,
    'circ_mv_yi': 0.61,
    'winner_rate': 0.97
}

def calc_composite_score(df, weights):
    """加权综合评分 - 使用Min-Max归一化"""
    score = pd.Series(0.0, index=df.index)
    
    for factor, weight in weights.items():
        if factor not in df.columns:
            continue
        
        factor_values = df[factor].copy()
        min_val = factor_values.min()
        max_val = factor_values.max()
        
        if max_val - min_val > 0:
            normalized = (factor_values - min_val) / (max_val - min_val)
        else:
            normalized = pd.Series(0.5, index=df.index)
        
        if factor in ['pb']:
            normalized = 1 - normalized
        
        score += normalized * weight
    
    return score

def select_by_quantile(df, score_col, quantile=0.8):
    """取评分最高的Top N%"""
    threshold = df[score_col].quantile(quantile)
    return df[df[score_col] >= threshold]

def backtest_with_quantile(executor, weights, quantile):
    """使用分位选股进行回测"""
    daily = executor.load_data()
    df = daily[daily['trade_date'] >= executor.start_date].copy()
    
    df['composite_score'] = np.nan
    
    for date in df['trade_date'].unique():
        day_mask = df['trade_date'] == date
        day_df = df[day_mask].copy()
        
        if len(day_df) > 0:
            day_scores = calc_composite_score(day_df, weights)
            df.loc[day_mask, 'composite_score'] = day_scores
    
    df['signal'] = False
    
    for date in df['trade_date'].unique():
        day_mask = df['trade_date'] == date
        day_df = df[day_mask].copy()
        
        if len(day_df) > 0 and day_df['composite_score'].notna().any():
            selected = select_by_quantile(day_df.dropna(subset=['composite_score']), 'composite_score', quantile)
            df.loc[selected.index, 'signal'] = True
    
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
        'quantile': quantile,
        'weights': weights
    }

def optimize_weights_and_quantile():
    """优化权重和分位参数"""
    executor = BacktestExecutor("20260101", "20260625")
    
    print("="*70)
    print("          综合评分策略优化")
    print("="*70)
    print(f"初始权重: {INITIAL_WEIGHTS}")
    print("="*70)
    
    results = []
    
    quantiles = [0.70, 0.75, 0.80, 0.85, 0.90]
    
    print(f"\n测试不同分位参数 ({quantiles}):")
    
    for quantile in quantiles:
        print(f"\n--- 分位: Top {int((1 - quantile) * 100)}% ---")
        
        result = backtest_with_quantile(executor, INITIAL_WEIGHTS, quantile)
        
        print(f"胜率: {result['win_rate']:.1%}")
        print(f"总收益: {result['total_return']:.1%}")
        print(f"最大回撤: {result['max_drawdown']:.1%}")
        print(f"信号数: {result['signal_count']} ({result['signal_per_day']:.1f}/天)")
        print(f"交易数: {result['trade_count']}")
        
        results.append(result)
    
    return results

def find_optimal_combination():
    """寻找最优参数组合"""
    executor = BacktestExecutor("20260101", "20260625")
    
    results = []
    
    quantiles = [0.70, 0.73, 0.75, 0.77, 0.80, 0.83, 0.85]
    
    weight_variants = [
        {'pb': 0.6, 'ret_20d': 1.5, 'circ_mv_yi': 0.6, 'winner_rate': 1.0},
        {'pb': 0.5, 'ret_20d': 1.8, 'circ_mv_yi': 0.5, 'winner_rate': 1.2},
        {'pb': 0.7, 'ret_20d': 1.2, 'circ_mv_yi': 0.7, 'winner_rate': 0.8},
        {'pb': 0.8, 'ret_20d': 1.0, 'circ_mv_yi': 0.8, 'winner_rate': 0.6},
    ]
    
    total_iterations = len(quantiles) * len(weight_variants)
    count = 0
    
    print(f"\n\n{'='*70}")
    print("          参数组合扫描")
    print("="*70)
    print(f"总组合数: {total_iterations}")
    print("="*70)
    
    for weights in weight_variants:
        for quantile in quantiles:
            count += 1
            print(f"\n[{count}/{total_iterations}] 权重:{weights}, 分位:{quantile}")
            
            result = backtest_with_quantile(executor, weights, quantile)
            
            print(f"  胜率: {result['win_rate']:.1%}, 信号: {result['signal_per_day']:.1f}/天, 回撤: {result['max_drawdown']:.1%}, 收益: {result['total_return']:.1%}")
            
            results.append(result)
    
    return results

def main():
    # 第一步：测试不同分位
    quantile_results = optimize_weights_and_quantile()
    
    # 第二步：寻找最优组合
    all_results = find_optimal_combination()
    
    # 分析结果
    print(f"\n\n{'='*70}")
    print("          结果分析")
    print("="*70)
    
    valid_results = [r for r in all_results if 
                     r['win_rate'] >= 0.55 and r['win_rate'] <= 0.65 and
                     r['signal_per_day'] >= 5 and r['signal_per_day'] <= 10 and
                     r['max_drawdown'] <= 0.20 and
                     r['trade_count'] >= 10]
    
    if valid_results:
        valid_results.sort(key=lambda x: -x['total_return'])
        
        print(f"\n✅ 找到 {len(valid_results)} 个满足约束的策略:")
        
        for i, r in enumerate(valid_results[:3], 1):
            print(f"\n【候选策略 {i}】")
            print(f"  分位: Top {int((1 - r['quantile']) * 100)}%")
            print(f"  权重: {r['weights']}")
            print(f"  胜率: {r['win_rate']:.1%}")
            print(f"  总收益: {r['total_return']:.1%}")
            print(f"  最大回撤: {r['max_drawdown']:.1%}")
            print(f"  信号数: {r['signal_per_day']:.1f}/天")
            print(f"  交易数: {r['trade_count']}")
        
        best = valid_results[0]
        
        output_path = os.path.join(os.path.dirname(__file__), 'win_rate_hunter', 'composite_strategy.json')
        strategy_data = {
            'name': 'Composite_Score_Strategy',
            'weights': best['weights'],
            'quantile': best['quantile'],
            'hold_days': 10,
            'backtest_result': {
                'win_rate': best['win_rate'],
                'total_return': best['total_return'],
                'max_drawdown': best['max_drawdown'],
                'signal_count': best['signal_count'],
                'signal_per_day': best['signal_per_day'],
                'trade_count': best['trade_count']
            }
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(strategy_data, f, indent=2, ensure_ascii=False)
        
        print(f"\n📄 最优策略已保存到: {output_path}")
    
    else:
        print("\n❌ 未找到满足所有约束的策略")
        
        all_results.sort(key=lambda x: (
            abs(x['win_rate'] - 0.575) +
            abs(x['signal_per_day'] - 7.5) +
            max(0, x['max_drawdown'] - 0.20)
        ))
        
        print("\n以下是最接近约束的策略:")
        for i, r in enumerate(all_results[:3], 1):
            print(f"\n【接近策略 {i}】")
            print(f"  分位: Top {int((1 - r['quantile']) * 100)}%")
            print(f"  权重: {r['weights']}")
            print(f"  胜率: {r['win_rate']:.1%} {'✅' if 0.55 <= r['win_rate'] <= 0.65 else '❌'}")
            print(f"  信号: {r['signal_per_day']:.1f}/天 {'✅' if 5 <= r['signal_per_day'] <= 10 else '❌'}")
            print(f"  回撤: {r['max_drawdown']:.1%} {'✅' if r['max_drawdown'] <= 0.20 else '❌'}")
            print(f"  收益: {r['total_return']:.1%}")

if __name__ == "__main__":
    main()
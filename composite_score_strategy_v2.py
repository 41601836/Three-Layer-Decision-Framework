# -*- coding: utf-8 -*-
"""
综合评分策略 v2 - 添加基础过滤和信号数限制
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

def apply_basic_filters(df):
    """应用基础过滤条件"""
    filtered = df.copy()
    
    filtered = filtered[(filtered['pct_chg'] >= -10) & (filtered['pct_chg'] <= 10)]
    
    filtered = filtered[(filtered['vol'] > 0) & (filtered['volume_ratio'] > 0.5)]
    
    filtered = filtered[filtered['circ_mv_yi'] >= 10]
    
    return filtered

def calc_composite_score(df, weights):
    """加权综合评分 - 使用排名归一化"""
    score = pd.Series(0.0, index=df.index)
    
    for factor, weight in weights.items():
        if factor not in df.columns:
            continue
        
        factor_values = df[factor].copy()
        
        if factor_values.isna().all():
            continue
        
        ranks = factor_values.rank(pct=True)
        
        if factor in ['pb']:
            ranks = 1 - ranks
        
        score += ranks * weight
    
    return score

def select_top_n(df, score_col, top_n=10):
    """取评分最高的前N只股票"""
    sorted_df = df.dropna(subset=[score_col]).sort_values(score_col, ascending=False)
    return sorted_df.head(top_n)

def backtest_with_top_n(executor, weights, top_n=10):
    """使用Top-N选股进行回测"""
    daily = executor.load_data()
    df = daily[daily['trade_date'] >= executor.start_date].copy()
    
    df['composite_score'] = np.nan
    df['signal'] = False
    
    for date in sorted(df['trade_date'].unique()):
        day_mask = df['trade_date'] == date
        day_df = df[day_mask].copy()
        
        day_df = apply_basic_filters(day_df)
        
        if len(day_df) > 0:
            day_df['_score'] = calc_composite_score(day_df, weights)
            
            selected = select_top_n(day_df, '_score', top_n)
            
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
        'top_n': top_n,
        'weights': weights
    }

def optimize_top_n():
    """优化每日选股数量"""
    executor = BacktestExecutor("20260101", "20260625")
    
    print("="*70)
    print("          综合评分策略 v2 - Top-N 优化")
    print("="*70)
    print(f"初始权重: {INITIAL_WEIGHTS}")
    print("="*70)
    
    results = []
    
    top_n_values = [3, 5, 8, 10, 12, 15, 20]
    
    print(f"\n测试不同每日选股数量 ({top_n_values}):")
    
    for top_n in top_n_values:
        print(f"\n--- Top-{top_n} ---")
        
        result = backtest_with_top_n(executor, INITIAL_WEIGHTS, top_n)
        
        print(f"胜率: {result['win_rate']:.1%}")
        print(f"总收益: {result['total_return']:.1%}")
        print(f"最大回撤: {result['max_drawdown']:.1%}")
        print(f"信号数: {result['signal_count']} ({result['signal_per_day']:.1f}/天)")
        print(f"交易数: {result['trade_count']}")
        
        results.append(result)
    
    return results

def find_optimal_weights():
    """寻找最优权重组合"""
    executor = BacktestExecutor("20260101", "20260625")
    
    results = []
    
    top_n_values = [5, 7, 10]
    
    weight_variants = [
        {'pb': 0.5, 'ret_20d': 1.5, 'circ_mv_yi': 0.5, 'winner_rate': 1.0},
        {'pb': 0.4, 'ret_20d': 2.0, 'circ_mv_yi': 0.4, 'winner_rate': 1.2},
        {'pb': 0.6, 'ret_20d': 1.2, 'circ_mv_yi': 0.6, 'winner_rate': 0.8},
        {'pb': 0.7, 'ret_20d': 1.0, 'circ_mv_yi': 0.7, 'winner_rate': 0.6},
        {'pb': 0.3, 'ret_20d': 2.2, 'circ_mv_yi': 0.3, 'winner_rate': 1.5},
    ]
    
    total_iterations = len(top_n_values) * len(weight_variants)
    count = 0
    
    print(f"\n\n{'='*70}")
    print("          参数组合扫描")
    print("="*70)
    print(f"总组合数: {total_iterations}")
    print("="*70)
    
    for top_n in top_n_values:
        for weights in weight_variants:
            count += 1
            print(f"\n[{count}/{total_iterations}] Top-{top_n}, 权重:{weights}")
            
            result = backtest_with_top_n(executor, weights, top_n)
            
            meets_constraints = (
                result['win_rate'] >= 0.55 and result['win_rate'] <= 0.65 and
                result['signal_per_day'] >= 5 and result['signal_per_day'] <= 10 and
                result['max_drawdown'] <= 0.20 and
                result['trade_count'] >= 10
            )
            
            status = "✅" if meets_constraints else "❌"
            print(f"  {status} 胜率: {result['win_rate']:.1%}, 信号: {result['signal_per_day']:.1f}/天, 回撤: {result['max_drawdown']:.1%}, 收益: {result['total_return']:.1%}")
            
            results.append(result)
    
    return results

def main():
    top_n_results = optimize_top_n()
    
    all_results = find_optimal_weights()
    
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
            print(f"  每日选股: Top-{r['top_n']}")
            print(f"  权重: {r['weights']}")
            print(f"  胜率: {r['win_rate']:.1%}")
            print(f"  总收益: {r['total_return']:.1%}")
            print(f"  最大回撤: {r['max_drawdown']:.1%}")
            print(f"  信号数: {r['signal_per_day']:.1f}/天")
            print(f"  交易数: {r['trade_count']}")
        
        best = valid_results[0]
        
        output_path = os.path.join(os.path.dirname(__file__), 'win_rate_hunter', 'composite_strategy_v2.json')
        strategy_data = {
            'name': 'Composite_Score_Strategy_v2',
            'weights': best['weights'],
            'top_n': best['top_n'],
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
            print(f"  每日选股: Top-{r['top_n']}")
            print(f"  权重: {r['weights']}")
            print(f"  胜率: {r['win_rate']:.1%} {'✅' if 0.55 <= r['win_rate'] <= 0.65 else '❌'}")
            print(f"  信号: {r['signal_per_day']:.1f}/天 {'✅' if 5 <= r['signal_per_day'] <= 10 else '❌'}")
            print(f"  回撤: {r['max_drawdown']:.1%} {'✅' if r['max_drawdown'] <= 0.20 else '❌'}")
            print(f"  收益: {r['total_return']:.1%}")

if __name__ == "__main__":
    main()
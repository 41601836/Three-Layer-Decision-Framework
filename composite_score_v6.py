# -*- coding: utf-8 -*-
"""
综合评分策略V6 - 修复单位问题
"""
import sys
import os
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

def calc_composite_score(df, weights, directions):
    """加权综合评分 - 使用Min-Max归一化"""
    score = pd.Series(0.0, index=df.index)
    
    for factor, weight in weights.items():
        if factor not in df.columns:
            continue
        
        factor_values = df[factor]
        min_val = factor_values.min()
        max_val = factor_values.max()
        
        if max_val - min_val > 0:
            normalized = (factor_values - min_val) / (max_val - min_val)
        else:
            normalized = 0.5
        
        if directions.get(factor, 'higher_better') == 'lower_better':
            normalized = 1 - normalized
        
        score += normalized * weight
    
    return score

def backtest_composite(executor, weights, directions, top_n=10, min_score_ratio=0.4):
    """回测综合评分策略"""
    daily = executor.load_data()
    df = daily[daily['trade_date'] >= executor.start_date].copy()
    
    df = df.dropna(subset=['close', 'pb', 'circ_mv', 'ret_20d'])
    
    df['signal'] = False
    
    total_weight = sum(weights.values())
    min_score = total_weight * min_score_ratio
    
    for date in sorted(df['trade_date'].unique()):
        day_mask = df['trade_date'] == date
        day_df = df[day_mask].copy()
        
        if len(day_df) >= top_n:
            day_df['_score'] = calc_composite_score(day_df, weights, directions)
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
        
        max_positions = top_n
        new_signals = [s for s in signals if s not in positions]
        
        for code in new_signals[:max_positions - len(positions)]:
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
        'signal_per_day': signal_per_day,
        'trade_count': trade_count,
        'top_n': top_n,
        'min_score_ratio': min_score_ratio
    }

def main():
    executor = BacktestExecutor("20260101", "20260625")
    
    print("="*70)
    print("          综合评分策略V6 - 使用正确数据")
    print("="*70)
    
    weights = {
        'pb': 1.5,
        'ret_20d': 2.0,
        'circ_mv': 1.0,
        'pct_chg': 1.5,
        'roe': 1.5,
        'volatility_20d': 1.0
    }
    
    directions = {
        'pb': 'lower_better',
        'ret_20d': 'higher_better',
        'circ_mv': 'higher_better',
        'pct_chg': 'higher_better',
        'roe': 'higher_better',
        'volatility_20d': 'lower_better'
    }
    
    results = []
    
    for top_n in [6, 8, 10]:
        for min_score_ratio in [0.35, 0.4, 0.45]:
            print(f"\n--- Top-{top_n}, 最小评分{min_score_ratio} ---")
            
            result = backtest_composite(executor, weights, directions, top_n, min_score_ratio)
            
            print(f"胜率: {result['win_rate']:.1%}")
            print(f"总收益: {result['total_return']:.1%}")
            print(f"最大回撤: {result['max_drawdown']:.1%}")
            print(f"信号数: {result['signal_per_day']:.1f}/天")
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
        
        print(f"Top-{r['top_n']}, 评分{r['min_score_ratio']}: 胜率={r['win_rate']:.1%}, 信号={r['signal_per_day']:.1f}/天, 回撤={r['max_drawdown']:.1%}, 收益={r['total_return']:.1%} {status}")

if __name__ == "__main__":
    main()
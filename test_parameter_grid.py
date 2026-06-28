# -*- coding: utf-8 -*-
"""
参数网格搜索 - 寻找胜率和信号数的平衡点
"""
import sys
import os
import json
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

def backtest(executor, weights, thresholds, min_score_ratio, max_signals, hold_days):
    daily = executor.load_data()
    df = daily[daily['trade_date'] >= executor.start_date].copy()
    df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
    
    factor_directions = {
        'pb': 'lower_better',
        'ret_20d': 'higher_better',
        'circ_mv_yi': 'higher_better',
        'winner_rate': 'higher_better'
    }
    
    df['signal'] = False
    total_weight = sum(weights.values())
    min_score = total_weight * min_score_ratio
    
    for date in sorted(df['trade_date'].unique()):
        day_mask = df['trade_date'] == date
        day_df = df[day_mask].copy()
        
        if len(day_df) > max_signals:
            day_df['_score'] = evaluate_threshold_strategy(day_df, weights, thresholds, factor_directions)
            
            qualified = day_df[day_df['_score'] >= min_score]
            
            if len(qualified) >= max_signals:
                selected = qualified.sort_values('_score', ascending=False).head(max_signals)
                df.loc[selected.index, 'signal'] = True
            elif len(qualified) > 0:
                df.loc[qualified.index, 'signal'] = True
    
    signal_count = df['signal'].sum()
    total_days = len(df['trade_date'].unique())
    signal_per_day = signal_count / total_days if total_days > 0 else 0
    
    trades = []
    positions = {}
    portfolio_value = 1.0
    max_portfolio_value = 1.0
    max_drawdown = 0.0
    
    for date in sorted(df['trade_date'].unique()):
        day_df = df[df['trade_date'] == date]
        signals = day_df[day_df['signal']]['ts_code'].tolist()
        
        closed = []
        for code, pos in list(positions.items()):
            pos['days'] += 1
            
            code_df = day_df[day_df['ts_code'] == code]
            current_price = float(code_df['close'].iloc[0]) if not code_df.empty else pos['entry_price']
            pos['current_price'] = current_price
            
            if pos['days'] >= hold_days:
                exit_price = current_price
                pnl = (exit_price - pos['entry_price']) / pos['entry_price']
                portfolio_value += pos['position'] * pnl
                trades.append({'return': pnl})
                closed.append(code)
        
        for code in closed:
            del positions[code]
        
        for code in signals[:max_signals - len(positions)]:
            if code not in positions:
                entry_price = float(day_df[day_df['ts_code'] == code]['close'].iloc[0])
                positions[code] = {'entry_price': entry_price, 'current_price': entry_price, 'days': 0, 'position': 1.0 / max_signals if max_signals > 0 else 0.1}
        
        current_portfolio = portfolio_value + sum(
            pos['position'] * (pos['current_price'] - pos['entry_price']) / pos['entry_price']
            for pos in positions.values()
        )
        
        max_portfolio_value = max(max_portfolio_value, current_portfolio)
        drawdown = 1 - current_portfolio / max_portfolio_value
        max_drawdown = max(max_drawdown, drawdown)
    
    if trades:
        trade_df = pd.DataFrame(trades)
        win_rate = (trade_df['return'] > 0).mean()
        total_return = portfolio_value - 1
        trade_count = len(trades)
    else:
        win_rate = 0.0
        total_return = 0.0
        trade_count = 0
    
    return {
        'win_rate': win_rate,
        'total_return': total_return,
        'max_drawdown': max_drawdown,
        'signal_per_day': signal_per_day,
        'trade_count': trade_count
    }

def main():
    executor = BacktestExecutor("20260101", "20260625")
    
    weights = {'pb': 0.8, 'ret_20d': 1.8, 'circ_mv_yi': 0.8, 'winner_rate': 1.0}
    
    parameter_grid = [
        {'min_score': 0.6, 'max_signals': 2, 'thresholds': {'pb': 0.75, 'ret_20d': 0.60, 'circ_mv_yi': 0.45, 'winner_rate': 0.35}},
        {'min_score': 0.55, 'max_signals': 3, 'thresholds': {'pb': 0.70, 'ret_20d': 0.58, 'circ_mv_yi': 0.42, 'winner_rate': 0.32}},
        {'min_score': 0.5, 'max_signals': 4, 'thresholds': {'pb': 0.68, 'ret_20d': 0.55, 'circ_mv_yi': 0.40, 'winner_rate': 0.30}},
        {'min_score': 0.45, 'max_signals': 5, 'thresholds': {'pb': 0.65, 'ret_20d': 0.52, 'circ_mv_yi': 0.38, 'winner_rate': 0.28}},
        {'min_score': 0.4, 'max_signals': 6, 'thresholds': {'pb': 0.62, 'ret_20d': 0.50, 'circ_mv_yi': 0.35, 'winner_rate': 0.25}},
        {'min_score': 0.5, 'max_signals': 3, 'thresholds': {'pb': 0.72, 'ret_20d': 0.58, 'circ_mv_yi': 0.43, 'winner_rate': 0.33}},
        {'min_score': 0.55, 'max_signals': 4, 'thresholds': {'pb': 0.70, 'ret_20d': 0.56, 'circ_mv_yi': 0.42, 'winner_rate': 0.31}},
    ]
    
    print("="*70)
    print("          参数网格搜索 - 寻找平衡点")
    print("="*70)
    print(f"{'配置':<10} {'胜率':<8} {'收益':<8} {'回撤':<8} {'信号':<6} {'状态'}")
    print("-"*70)
    
    results = []
    
    for i, params in enumerate(parameter_grid):
        result = backtest(executor, weights, params['thresholds'], params['min_score'], params['max_signals'], 10)
        
        meets_target = result['win_rate'] >= 0.60 and result['signal_per_day'] >= 1.5
        status = "✅" if meets_target else "❌"
        
        print(f"组合{i+1:<8} {result['win_rate']:>5.1%}  {result['total_return']:>5.1%}  {result['max_drawdown']:>5.1%}  {result['signal_per_day']:>4.1f} {status}")
        
        results.append({'params': params, **result})
    
    print("\n" + "="*70)
    print("          最佳组合")
    print("="*70)
    
    valid_results = [r for r in results if r['win_rate'] >= 0.55 and r['signal_per_day'] >= 1]
    if valid_results:
        best = max(valid_results, key=lambda x: x['win_rate'] * 100 + x['signal_per_day'])
        
        print(f"最佳参数组合:")
        print(f"  最小评分比例: {best['params']['min_score']}")
        print(f"  每日最大信号数: {best['params']['max_signals']}")
        print(f"  阈值: {best['params']['thresholds']}")
        print()
        print(f"回测结果:")
        print(f"  🎯 胜率: {best['win_rate']:.1%}")
        print(f"  📈 总收益: {best['total_return']:.1%}")
        print(f"  🛡️ 最大回撤: {best['max_drawdown']:.1%}")
        print(f"  📊 日均信号数: {best['signal_per_day']:.1f}个")
        print(f"  🔢 交易次数: {best['trade_count']}次")
    else:
        print("未找到满足条件的参数组合")

if __name__ == "__main__":
    main()
# -*- coding: utf-8 -*-
"""
基于配置文件运行回测
"""
import sys
import os
import json
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

def load_config(config_path):
    """加载策略配置"""
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def evaluate_threshold_strategy(df, weights, thresholds, factor_directions):
    """评估阈值策略"""
    score = pd.Series(0.0, index=df.index)
    
    for i, factor in enumerate(list(weights.keys())):
        weight = weights[factor]
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

def backtest_with_config(executor, config):
    """使用配置运行回测"""
    daily = executor.load_data()
    df = daily[daily['trade_date'] >= executor.start_date].copy()
    df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
    
    weights = dict(zip(config['factors'], config['weights']))
    thresholds = config.get('thresholds', {})
    min_score_ratio = config.get('min_score', 0.5)
    max_signals = config.get('max_signals_per_day', 3)
    hold_days = config.get('hold_days', 10)
    
    factor_directions = {
        'pb': 'lower_better',
        'ret_20d': 'higher_better',
        'circ_mv_yi': 'higher_better',
        'winner_rate': 'higher_better',
        'relative_strength': 'higher_better',
        'roe': 'higher_better',
        'net_main_intensity': 'higher_better'
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
                
                trades.append({
                    'entry_date': pos['entry_date'],
                    'exit_date': date,
                    'entry_price': pos['entry_price'],
                    'exit_price': exit_price,
                    'return': pnl
                })
                closed.append(code)
        
        for code in closed:
            del positions[code]
        
        for code in signals[:max_signals - len(positions)]:
            if code not in positions:
                entry_price = float(day_df[day_df['ts_code'] == code]['close'].iloc[0])
                positions[code] = {
                    'entry_date': date,
                    'entry_price': entry_price,
                    'current_price': entry_price,
                    'days': 0,
                    'position': 1.0 / max_signals if max_signals > 0 else 0.1
                }
        
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
    config_path = 'release/v1.1_85pct/strategy_config.json'
    
    if not os.path.exists(config_path):
        print(f"配置文件不存在: {config_path}")
        return
    
    config = load_config(config_path)
    executor = BacktestExecutor("20260101", "20260625")
    
    print("="*70)
    print("          基于配置文件的回测")
    print("="*70)
    print(f"配置文件: {config_path}")
    print()
    print(f"策略参数:")
    print(f"  最小评分比例: {config['min_score']}")
    print(f"  每日最大信号数: {config['max_signals_per_day']}")
    print(f"  持有周期: {config['hold_days']}天")
    print()
    print(f"因子权重:")
    for factor, weight in zip(config['factors'], config['weights']):
        print(f"  {factor}: {weight}")
    print()
    if 'thresholds' in config:
        print(f"因子阈值:")
        for factor, threshold in config['thresholds'].items():
            print(f"  {factor}: {threshold:.4f}")
    print()
    
    result = backtest_with_config(executor, config)
    
    print(f"回测结果:")
    print(f"  🎯 胜率: {result['win_rate']:.1%}")
    print(f"  📈 总收益: {result['total_return']:.1%}")
    print(f"  🛡️ 最大回撤: {result['max_drawdown']:.1%}")
    print(f"  📊 日均信号数: {result['signal_per_day']:.1f}个")
    print(f"  🔢 交易次数: {result['trade_count']}次")
    print()
    
    meets_target = (
        result['win_rate'] >= 0.60 and
        result['signal_per_day'] >= 1
    )
    
    if meets_target:
        print("✅ 策略满足目标要求！")
    else:
        print("⚠️ 策略未完全满足目标要求")
        if result['win_rate'] < 0.60:
            print("   - 胜率低于60%目标")

if __name__ == "__main__":
    main()
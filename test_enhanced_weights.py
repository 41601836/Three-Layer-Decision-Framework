# -*- coding: utf-8 -*-
"""
快速测试增强因子策略的权重组合
"""
import sys
import os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

def calculate_rsi(df, period=14):
    delta = df['close'].diff(1)
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_macd(df, fast_period=12, slow_period=26, signal_period=9):
    ema_fast = df['close'].ewm(span=fast_period, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow_period, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal = macd.ewm(span=signal_period, adjust=False).mean()
    return macd, signal

def calculate_bollinger_bands(df, period=20):
    sma = df['close'].rolling(window=period).mean()
    std = df['close'].rolling(window=period).std()
    upper_band = sma + 2 * std
    lower_band = sma - 2 * std
    position = (df['close'] - lower_band) / (upper_band - lower_band + 1e-10)
    return position

def calculate_volume_ratio(df, period=5):
    volume_ma5 = df['vol'].rolling(window=period).mean()
    volume_ma20 = df['vol'].rolling(window=20).mean()
    ratio = volume_ma5 / (volume_ma20 + 1e-10)
    return ratio

def generate_factors(df):
    df = df.copy()
    df['rsi'] = df.groupby('ts_code', group_keys=False).apply(lambda x: calculate_rsi(x))
    
    def apply_macd(x):
        macd, signal = calculate_macd(x)
        return pd.DataFrame({'macd': macd, 'macd_signal': signal})
    
    macd_result = df.groupby('ts_code', group_keys=False).apply(apply_macd)
    df[['macd', 'macd_signal']] = macd_result.values
    
    df['bb_position'] = df.groupby('ts_code', group_keys=False).apply(lambda x: calculate_bollinger_bands(x))
    df['volume_ratio'] = df.groupby('ts_code', group_keys=False).apply(lambda x: calculate_volume_ratio(x))
    
    return df

def normalize_factor(factor_values, direction):
    min_val = factor_values.min()
    max_val = factor_values.max()
    
    if max_val - min_val > 0:
        normalized = (factor_values - min_val) / (max_val - min_val)
    else:
        normalized = pd.Series(0.5, index=factor_values.index)
    
    if direction == 'lower_better':
        normalized = 1 - normalized
    elif direction == 'middle_better':
        normalized = 1 - abs(normalized - 0.5) * 2
    
    return normalized

def backtest(executor, weights, directions, daily_max_signals=3, hold_days=10):
    daily = executor.load_data()
    df = daily[daily['trade_date'] >= executor.start_date].copy()
    df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
    df = generate_factors(df)
    
    df['signal'] = False
    
    for date in sorted(df['trade_date'].unique()):
        day_mask = df['trade_date'] == date
        day_df = df[day_mask].copy()
        
        if len(day_df) >= 20:
            score = pd.Series(0.0, index=day_df.index)
            total_weight = sum(weights.values())
            
            for factor, weight in weights.items():
                if factor not in day_df.columns:
                    continue
                
                factor_values = day_df[factor].copy()
                direction = directions.get(factor, 'higher_better')
                normalized = normalize_factor(factor_values, direction)
                score += normalized * weight
            
            day_df['_score'] = score / total_weight
            threshold = day_df['_score'].quantile(0.75)
            qualified = day_df[day_df['_score'] >= threshold]
            
            if len(qualified) > 0:
                selected = qualified.sort_values('_score', ascending=False).head(daily_max_signals)
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
            if pos['days'] >= hold_days:
                code_df = day_df[day_df['ts_code'] == code]
                exit_price = float(code_df['close'].iloc[0]) if not code_df.empty else pos['entry_price']
                trades.append({'return': (exit_price - pos['entry_price']) / pos['entry_price']})
                closed.append(code)
        
        for code in closed:
            del positions[code]
        
        for code in signals[:daily_max_signals - len(positions)]:
            if code not in positions:
                entry_price = float(day_df[day_df['ts_code'] == code]['close'].iloc[0])
                positions[code] = {'entry_date': date, 'entry_price': entry_price, 'days': 0}
    
    if trades:
        trade_df = pd.DataFrame(trades)
        win_rate = (trade_df['return'] > 0).mean()
        total_return = (1 + trade_df['return']).prod() - 1
        trade_count = len(trades)
        
        equity = (1 + trade_df['return']).cumprod()
        max_drawdown = (1 - equity / equity.cummax()).max()
    else:
        win_rate = 0.0
        total_return = 0.0
        trade_count = 0
        max_drawdown = 0.0
    
    return {
        'win_rate': win_rate,
        'total_return': total_return,
        'max_drawdown': max_drawdown,
        'signal_per_day': signal_per_day,
        'trade_count': trade_count
    }

def main():
    executor = BacktestExecutor("20260101", "20260625")
    
    directions = {
        'pb': 'lower_better',
        'ret_20d': 'higher_better',
        'circ_mv_yi': 'higher_better',
        'winner_rate': 'higher_better',
        'rsi': 'middle_better',
        'macd_signal': 'higher_better',
        'bb_position': 'middle_better',
        'volume_ratio': 'higher_better'
    }
    
    weight_candidates = [
        {
            'name': '动量优先',
            'weights': {
                'pb': 1.0,
                'ret_20d': 2.0,
                'circ_mv_yi': 0.8,
                'winner_rate': 1.0,
                'rsi': 0.8,
                'macd_signal': 1.5,
                'bb_position': 0.5,
                'volume_ratio': 1.2
            }
        },
        {
            'name': '质量优先',
            'weights': {
                'pb': 1.5,
                'ret_20d': 1.0,
                'circ_mv_yi': 1.2,
                'winner_rate': 1.5,
                'rsi': 1.0,
                'macd_signal': 0.8,
                'bb_position': 0.8,
                'volume_ratio': 0.8
            }
        },
        {
            'name': '均衡配置',
            'weights': {
                'pb': 1.2,
                'ret_20d': 1.5,
                'circ_mv_yi': 1.0,
                'winner_rate': 1.2,
                'rsi': 1.0,
                'macd_signal': 1.0,
                'bb_position': 0.8,
                'volume_ratio': 1.0
            }
        },
        {
            'name': '技术因子增强',
            'weights': {
                'pb': 0.5,
                'ret_20d': 1.0,
                'circ_mv_yi': 0.5,
                'winner_rate': 0.5,
                'rsi': 1.5,
                'macd_signal': 1.5,
                'bb_position': 1.2,
                'volume_ratio': 1.5
            }
        },
        {
            'name': '高门槛精选',
            'weights': {
                'pb': 2.0,
                'ret_20d': 2.0,
                'circ_mv_yi': 1.5,
                'winner_rate': 2.0,
                'rsi': 1.0,
                'macd_signal': 1.0,
                'bb_position': 0.5,
                'volume_ratio': 1.0
            }
        }
    ]
    
    print("="*70)
    print("          增强因子策略 - 权重组合测试")
    print("="*70)
    print(f"目标: 胜率≥60%, 信号≥2个/天")
    print()
    
    results = []
    
    for candidate in weight_candidates:
        print(f"--- {candidate['name']} ---")
        
        result = backtest(executor, candidate['weights'], directions)
        
        print(f"胜率: {result['win_rate']:.1%}")
        print(f"总收益: {result['total_return']:.1%}")
        print(f"最大回撤: {result['max_drawdown']:.1%}")
        print(f"日均信号: {result['signal_per_day']:.1f}个")
        print(f"交易次数: {result['trade_count']}")
        
        meets_target = result['win_rate'] >= 0.60 and result['signal_per_day'] >= 2
        if meets_target:
            print("✅ 满足目标！")
        
        print()
        results.append({'name': candidate['name'], **result})
    
    print("="*70)
    print("          结果汇总")
    print("="*70)
    print(f"{'策略':<12} {'胜率':<8} {'收益':<8} {'回撤':<8} {'信号':<6}")
    print("-"*70)
    
    for r in results:
        status = "✅" if r['win_rate'] >= 0.60 and r['signal_per_day'] >= 2 else "❌"
        print(f"{r['name']:<12} {r['win_rate']:>5.1%}  {r['total_return']:>5.1%}  {r['max_drawdown']:>5.1%}  {r['signal_per_day']:>4.1f} {status}")

if __name__ == "__main__":
    main()
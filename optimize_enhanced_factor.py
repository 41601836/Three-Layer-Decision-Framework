# -*- coding: utf-8 -*-
"""
优化增强因子策略 - 使用遗传算法优化权重
目标: 胜率≥60%, 信号≥2个/天
"""
import sys
import os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

class EnhancedFactorOptimizer:
    def __init__(self):
        self.factors = ['pb', 'ret_20d', 'circ_mv_yi', 'winner_rate', 'rsi', 'macd_signal', 'bb_position', 'volume_ratio']
        
        self.factor_directions = {
            'pb': 'lower_better',
            'ret_20d': 'higher_better',
            'circ_mv_yi': 'higher_better',
            'winner_rate': 'higher_better',
            'rsi': 'middle_better',
            'macd_signal': 'higher_better',
            'bb_position': 'middle_better',
            'volume_ratio': 'higher_better'
        }
        
        self.daily_max_signals = 3
        self.position_per_signal = 0.08
        self.hold_days = 10
        self.quantile_threshold = 0.75
    
    def calculate_rsi(self, df, period=14):
        delta = df['close'].diff(1)
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def calculate_macd(self, df, fast_period=12, slow_period=26, signal_period=9):
        ema_fast = df['close'].ewm(span=fast_period, adjust=False).mean()
        ema_slow = df['close'].ewm(span=slow_period, adjust=False).mean()
        macd = ema_fast - ema_slow
        signal = macd.ewm(span=signal_period, adjust=False).mean()
        return macd, signal
    
    def calculate_bollinger_bands(self, df, period=20):
        sma = df['close'].rolling(window=period).mean()
        std = df['close'].rolling(window=period).std()
        upper_band = sma + 2 * std
        lower_band = sma - 2 * std
        position = (df['close'] - lower_band) / (upper_band - lower_band + 1e-10)
        return position
    
    def calculate_volume_ratio(self, df, period=5):
        volume_ma5 = df['vol'].rolling(window=period).mean()
        volume_ma20 = df['vol'].rolling(window=20).mean()
        ratio = volume_ma5 / (volume_ma20 + 1e-10)
        return ratio
    
    def generate_factors(self, df):
        df = df.copy()
        df['rsi'] = df.groupby('ts_code', group_keys=False).apply(lambda x: self.calculate_rsi(x.drop('ts_code', axis=1)) if 'ts_code' in x.columns else self.calculate_rsi(x))
        
        def apply_macd(x):
            x = x.drop('ts_code', axis=1) if 'ts_code' in x.columns else x
            macd, signal = self.calculate_macd(x)
            return pd.DataFrame({'macd': macd, 'macd_signal': signal})
        
        macd_result = df.groupby('ts_code', group_keys=False).apply(apply_macd)
        df[['macd', 'macd_signal']] = macd_result.values
        
        df['bb_position'] = df.groupby('ts_code', group_keys=False).apply(lambda x: self.calculate_bollinger_bands(x.drop('ts_code', axis=1)) if 'ts_code' in x.columns else self.calculate_bollinger_bands(x))
        df['volume_ratio'] = df.groupby('ts_code', group_keys=False).apply(lambda x: self.calculate_volume_ratio(x.drop('ts_code', axis=1)) if 'ts_code' in x.columns else self.calculate_volume_ratio(x))
        
        return df
    
    def normalize_factor(self, factor_values, direction):
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
    
    def evaluate_weights(self, weights, executor):
        daily = executor.load_data()
        df = daily[daily['trade_date'] >= executor.start_date].copy()
        df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
        df = self.generate_factors(df)
        
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
                    direction = self.factor_directions.get(factor, 'higher_better')
                    normalized = self.normalize_factor(factor_values, direction)
                    score += normalized * weight
                
                day_df['_score'] = score / total_weight
                threshold = day_df['_score'].quantile(self.quantile_threshold)
                qualified = day_df[day_df['_score'] >= threshold]
                
                if len(qualified) > 0:
                    selected = qualified.sort_values('_score', ascending=False).head(self.daily_max_signals)
                    df.loc[selected.index, 'signal'] = True
        
        signal_count = df['signal'].sum()
        total_days = len(df['trade_date'].unique())
        signal_per_day = signal_count / total_days if total_days > 0 else 0
        
        if signal_per_day < 2:
            return {'win_rate': 0, 'signal_per_day': signal_per_day, 'fitness': -1}
        
        trades = []
        positions = {}
        
        for date in sorted(df['trade_date'].unique()):
            day_df = df[df['trade_date'] == date]
            signals = day_df[day_df['signal']]['ts_code'].tolist()
            
            closed = []
            for code, pos in list(positions.items()):
                pos['days'] += 1
                if pos['days'] >= self.hold_days:
                    code_df = day_df[day_df['ts_code'] == code]
                    exit_price = float(code_df['close'].iloc[0]) if not code_df.empty else pos['entry_price']
                    trades.append({'return': (exit_price - pos['entry_price']) / pos['entry_price']})
                    closed.append(code)
            
            for code in closed:
                del positions[code]
            
            for code in signals[:self.daily_max_signals - len(positions)]:
                if code not in positions:
                    entry_price = float(day_df[day_df['ts_code'] == code]['close'].iloc[0])
                    positions[code] = {'entry_date': date, 'entry_price': entry_price, 'days': 0}
        
        if trades:
            trade_df = pd.DataFrame(trades)
            win_rate = (trade_df['return'] > 0).mean()
            total_return = (1 + trade_df['return']).prod() - 1
            trade_count = len(trades)
        else:
            win_rate = 0.0
            total_return = 0.0
            trade_count = 0
        
        fitness = win_rate * 100 + signal_per_day * 10 + total_return * 100
        
        return {
            'win_rate': win_rate,
            'total_return': total_return,
            'signal_per_day': signal_per_day,
            'trade_count': trade_count,
            'fitness': fitness
        }
    
    def genetic_optimization(self, executor, generations=10, population_size=20):
        population = []
        
        for _ in range(population_size):
            weights = {factor: np.random.uniform(0.5, 2.0) for factor in self.factors}
            population.append(weights)
        
        best_weights = None
        best_fitness = -np.inf
        best_result = None
        
        for gen in range(generations):
            scores = []
            
            for weights in population:
                result = self.evaluate_weights(weights, executor)
                scores.append((result['fitness'], weights, result))
            
            scores.sort(reverse=True, key=lambda x: x[0])
            
            if scores[0][0] > best_fitness:
                best_fitness = scores[0][0]
                best_weights = scores[0][1]
                best_result = scores[0][2]
            
            print(f"世代 {gen+1}/{generations}: 最佳适应度={best_fitness:.2f}, 胜率={best_result['win_rate']:.1%}, 信号={best_result['signal_per_day']:.1f}")
            
            new_population = [scores[0][1], scores[1][1]]
            
            while len(new_population) < population_size:
                idx1, idx2 = np.random.choice(len(scores[:10]), size=2, replace=False)
                parent1, parent2 = scores[idx1][1], scores[idx2][1]
                
                child = {}
                for factor in self.factors:
                    if np.random.random() < 0.5:
                        child[factor] = parent1[factor]
                    else:
                        child[factor] = parent2[factor]
                
                if np.random.random() < 0.2:
                    factor_to_mutate = np.random.choice(self.factors)
                    child[factor_to_mutate] = np.random.uniform(0.5, 2.0)
                
                new_population.append(child)
            
            population = new_population
        
        return best_weights, best_result

def main():
    executor = BacktestExecutor("20260101", "20260625")
    optimizer = EnhancedFactorOptimizer()
    
    print("="*70)
    print("          增强因子策略 - 遗传算法优化")
    print("="*70)
    print(f"目标: 胜率≥60%, 信号≥2个/天")
    print(f"优化因子: {optimizer.factors}")
    print()
    
    best_weights, best_result = optimizer.genetic_optimization(executor, generations=15, population_size=25)
    
    print("\n" + "="*70)
    print("          优化结果")
    print("="*70)
    print(f"最佳权重:")
    for factor, weight in sorted(best_weights.items(), key=lambda x: -x[1]):
        print(f"  {factor}: {weight:.2f}")
    
    print(f"\n回测结果:")
    print(f"  🎯 胜率: {best_result['win_rate']:.1%}")
    print(f"  📈 总收益: {best_result['total_return']:.1%}")
    print(f"  📊 日均信号数: {best_result['signal_per_day']:.1f}个")
    print(f"  🔢 交易次数: {best_result['trade_count']}次")
    
    meets_target = (
        best_result['win_rate'] >= 0.60 and
        best_result['signal_per_day'] >= 2
    )
    
    if meets_target:
        print("\n✅ 策略满足所有目标要求！")
    else:
        print("\n⚠️ 策略未完全满足目标要求")
        
        if best_result['win_rate'] < 0.60:
            print("   - 胜率低于60%目标")
        if best_result['signal_per_day'] < 2:
            print("   - 信号数低于2个/天目标")

if __name__ == "__main__":
    main()
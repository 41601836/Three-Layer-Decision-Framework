# -*- coding: utf-8 -*-
"""
黄金平衡模式 - 遗传算法优化器
目标：胜率≥65% + 信号≥2个/天 + 回撤≤5%
因子池：pb, ret_20d, circ_mv_yi, winner_rate, relative_strength, roe, net_main_intensity
"""
import sys
import os
import numpy as np
import pandas as pd
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

class GoldBalanceOptimizer:
    def __init__(self):
        self.factors = ['pb', 'ret_20d', 'circ_mv_yi', 'winner_rate', 'relative_strength', 'roe', 'net_main_intensity']
        
        self.factor_directions = {
            'pb': 'lower_better',
            'ret_20d': 'higher_better',
            'circ_mv_yi': 'higher_better',
            'winner_rate': 'higher_better',
            'relative_strength': 'higher_better',
            'roe': 'higher_better',
            'net_main_intensity': 'higher_better'
        }
        
        self.targets = {
            'win_rate': 0.65,
            'signals_per_day': 2,
            'max_drawdown': 0.05
        }
        
        self.population_size = 50
        self.generations = 50
        self.mutation_rate = 0.1
        self.crossover_rate = 0.7
        
        self.executor = BacktestExecutor("20260101", "20260625")
    
    def normalize_factor(self, factor_values, direction):
        min_val = factor_values.min()
        max_val = factor_values.max()
        
        if max_val - min_val > 0:
            normalized = (factor_values - min_val) / (max_val - min_val)
        else:
            normalized = pd.Series(0.5, index=factor_values.index)
        
        if direction == 'lower_better':
            normalized = 1 - normalized
        
        return normalized
    
    def calculate_score(self, df, weights):
        score = pd.Series(0.0, index=df.index)
        total_weight = sum(weights.values())
        
        for factor, weight in weights.items():
            if factor not in df.columns:
                continue
            
            factor_values = df[factor].copy()
            direction = self.factor_directions.get(factor, 'higher_better')
            normalized = self.normalize_factor(factor_values, direction)
            score += normalized * weight
        
        return score / total_weight if total_weight > 0 else score
    
    def evaluate_individual(self, individual):
        weights = dict(zip(self.factors, individual))
        
        daily = self.executor.load_data()
        df = daily[daily['trade_date'] >= self.executor.start_date].copy()
        df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
        
        df['relative_strength'] = df['pct_chg'].rolling(20).mean().fillna(0)
        df['roe'] = df.get('roe', pd.Series(0.1, index=df.index))
        df['net_main_intensity'] = np.random.uniform(-0.1, 0.1, len(df))
        
        df['signal'] = False
        daily_max_signals = 3
        
        for date in sorted(df['trade_date'].unique()):
            day_mask = df['trade_date'] == date
            day_df = df[day_mask].copy()
            
            if len(day_df) >= 20:
                day_df['_score'] = self.calculate_score(day_df, weights)
                threshold = day_df['_score'].quantile(0.75)
                qualified = day_df[day_df['_score'] >= threshold]
                
                if len(qualified) > 0:
                    selected = qualified.sort_values('_score', ascending=False).head(daily_max_signals)
                    df.loc[selected.index, 'signal'] = True
        
        signal_count = df['signal'].sum()
        total_days = len(df['trade_date'].unique())
        signals_per_day = signal_count / total_days if total_days > 0 else 0
        
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
                
                if pos['days'] >= 10:
                    exit_price = current_price
                    pnl = (exit_price - pos['entry_price']) / pos['entry_price']
                    portfolio_value += pos['position'] * pnl
                    trades.append({'return': pnl})
                    closed.append(code)
            
            for code in closed:
                del positions[code]
            
            for code in signals[:daily_max_signals - len(positions)]:
                if code not in positions:
                    entry_price = float(day_df[day_df['ts_code'] == code]['close'].iloc[0])
                    positions[code] = {'entry_price': entry_price, 'current_price': entry_price, 'days': 0, 'position': 0.08}
            
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
            'signals_per_day': signals_per_day,
            'trade_count': trade_count
        }
    
    def calculate_fitness(self, result):
        wr = result['win_rate']
        spd = result['signals_per_day']
        dd = result['max_drawdown']
        tr = result['total_return']
        
        score = 0
        
        if wr >= self.targets['win_rate']:
            score += wr * 50
        else:
            score += wr * 20
        
        if spd >= self.targets['signals_per_day']:
            score += spd * 10
        else:
            score += spd * 5
        
        if dd <= self.targets['max_drawdown']:
            score += (1 - dd) * 20
        else:
            score += (1 - dd) * 5
        
        score += tr * 20
        
        penalty = 0
        if wr < self.targets['win_rate']:
            penalty += (self.targets['win_rate'] - wr) * 30
        if spd < self.targets['signals_per_day']:
            penalty += (self.targets['signals_per_day'] - spd) * 20
        if dd > self.targets['max_drawdown']:
            penalty += (dd - self.targets['max_drawdown']) * 30
        
        return max(0, score - penalty)
    
    def create_individual(self):
        weights = np.random.uniform(0.1, 2.0, len(self.factors))
        return weights / weights.sum() * len(self.factors)
    
    def crossover(self, parent1, parent2):
        if random.random() < self.crossover_rate:
            point = random.randint(1, len(self.factors) - 1)
            child = np.concatenate([parent1[:point], parent2[point:]])
        else:
            child = parent1.copy()
        
        return child / child.sum() * len(self.factors)
    
    def mutate(self, individual):
        for i in range(len(individual)):
            if random.random() < self.mutation_rate:
                individual[i] = max(0.01, min(3.0, individual[i] + np.random.normal(0, 0.2)))
        
        return individual / individual.sum() * len(self.factors)
    
    def select_parents(self, population, fitness_scores):
        total_fitness = sum(fitness_scores)
        if total_fitness == 0:
            return random.choice(population), random.choice(population)
        
        probabilities = [f / total_fitness for f in fitness_scores]
        idx1, idx2 = np.random.choice(len(population), size=2, replace=False, p=probabilities)
        return population[idx1], population[idx2]
    
    def optimize(self):
        print("="*70)
        print("          黄金平衡模式 - 遗传算法优化器")
        print("="*70)
        print(f"目标: 胜率≥{self.targets['win_rate']*100:.0f}% + 信号≥{self.targets['signals_per_day']}个/天 + 回撤≤{self.targets['max_drawdown']*100:.0f}%")
        print(f"因子池: {', '.join(self.factors)}")
        print()
        
        population = [self.create_individual() for _ in range(self.population_size)]
        best_fitness = 0
        best_individual = None
        best_result = None
        
        for generation in range(self.generations):
            fitness_scores = []
            results = []
            
            for individual in population:
                result = self.evaluate_individual(individual)
                results.append(result)
                fitness = self.calculate_fitness(result)
                fitness_scores.append(fitness)
            
            current_best_idx = np.argmax(fitness_scores)
            current_best_fitness = fitness_scores[current_best_idx]
            current_best_result = results[current_best_idx]
            current_best_individual = population[current_best_idx]
            
            if current_best_fitness > best_fitness:
                best_fitness = current_best_fitness
                best_individual = current_best_individual.copy()
                best_result = current_best_result.copy()
            
            avg_fitness = np.mean(fitness_scores)
            
            if (generation + 1) % 5 == 0 or generation == 0:
                print(f"第 {generation+1:3d} 代 | 最优适应度: {best_fitness:.2f} | 平均适应度: {avg_fitness:.2f}")
                if best_result:
                    print(f"        🎯 胜率: {best_result['win_rate']:.1%} | 📊 信号: {best_result['signals_per_day']:.1f} | 🛡️ 回撤: {best_result['max_drawdown']:.1%} | 📈 收益: {best_result['total_return']:.1%}")
            
            new_population = [best_individual.copy()]
            
            while len(new_population) < self.population_size:
                parent1, parent2 = self.select_parents(population, fitness_scores)
                child = self.crossover(parent1, parent2)
                child = self.mutate(child)
                new_population.append(child)
            
            population = new_population
        
        print()
        print("="*70)
        print("          优化完成")
        print("="*70)
        
        if best_result:
            print(f"最优权重:")
            for factor, weight in zip(self.factors, best_individual):
                print(f"  {factor}: {weight:.3f}")
            print()
            print(f"回测结果:")
            print(f"  🎯 胜率: {best_result['win_rate']:.1%}")
            print(f"  📈 总收益: {best_result['total_return']:.1%}")
            print(f"  🛡️ 最大回撤: {best_result['max_drawdown']:.1%}")
            print(f"  📊 日均信号数: {best_result['signals_per_day']:.1f}个")
            print(f"  🔢 交易次数: {best_result['trade_count']}次")
            print()
            
            meets_target = (
                best_result['win_rate'] >= self.targets['win_rate'] and
                best_result['signals_per_day'] >= self.targets['signals_per_day'] and
                best_result['max_drawdown'] <= self.targets['max_drawdown']
            )
            
            if meets_target:
                print("✅ 策略满足所有目标要求！")
            else:
                print("⚠️ 策略未完全满足目标要求")
                if best_result['win_rate'] < self.targets['win_rate']:
                    print(f"   - 胜率 {best_result['win_rate']:.1%} < {self.targets['win_rate']*100:.0f}%")
                if best_result['signals_per_day'] < self.targets['signals_per_day']:
                    print(f"   - 信号 {best_result['signals_per_day']:.1f} < {self.targets['signals_per_day']}")
                if best_result['max_drawdown'] > self.targets['max_drawdown']:
                    print(f"   - 回撤 {best_result['max_drawdown']:.1%} > {self.targets['max_drawdown']*100:.0f}%")
        else:
            print("未找到有效策略")
        
        return {
            'weights': dict(zip(self.factors, best_individual)),
            'result': best_result
        }

def main():
    optimizer = GoldBalanceOptimizer()
    optimizer.optimize()

if __name__ == "__main__":
    main()
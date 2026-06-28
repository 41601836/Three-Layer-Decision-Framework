# -*- coding: utf-8 -*-
"""
策略类和策略生成器
"""
from dataclasses import dataclass
from typing import List, Dict, Any
import numpy as np
import pandas as pd
from .factor_pool import Factor, FactorPool

@dataclass
class Strategy:
    name: str
    factors: List[Factor]
    weights: List[float]
    thresholds: Dict[str, float]
    hold_days: int = 10
    generation: int = 0
    
    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        """评估策略，生成评分"""
        score = pd.Series(0.0, index=df.index)
        
        for factor, weight in zip(self.factors, self.weights):
            factor_values = factor.compute(df)
            threshold = self.thresholds.get(factor.name, factor.default_threshold)
            
            if factor.direction == 'higher_better':
                mask = factor_values >= threshold
            else:
                mask = factor_values <= threshold
            
            score += mask.astype(float) * weight
        
        return score
    
    def generate_signal(self, df: pd.DataFrame, max_per_day: int = 50) -> pd.Series:
        """生成交易信号（每日最多选max_per_day只股票）"""
        score = self.evaluate(df)
        df['_score'] = score
        df['_signal_raw'] = score >= sum(self.weights) * 0.7
        
        signal = pd.Series(False, index=df.index)
        
        for date in df['trade_date'].unique():
            day_mask = df['trade_date'] == date
            day_df = df[day_mask & df['_signal_raw']]
            
            if len(day_df) > 0:
                # 按评分排序，选取前max_per_day只
                top_stocks = day_df.sort_values('_score', ascending=False).head(max_per_day)
                signal.loc[top_stocks.index] = True
        
        return signal
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'name': self.name,
            'factors': [f.name for f in self.factors],
            'weights': self.weights,
            'thresholds': self.thresholds,
            'hold_days': self.hold_days,
            'generation': self.generation
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], factor_pool: FactorPool) -> 'Strategy':
        """从字典创建策略"""
        factors = [factor_pool.get_factor_by_name(name) for name in data['factors']]
        factors = [f for f in factors if f is not None]
        return cls(
            name=data['name'],
            factors=factors,
            weights=data['weights'],
            thresholds=data['thresholds'],
            hold_days=data.get('hold_days', 10),
            generation=data.get('generation', 0)
        )

class StrategyGenerator:
    def __init__(self, factor_pool: FactorPool):
        self.factor_pool = factor_pool
        self.counter = 0
    
    def generate_random(self) -> Strategy:
        """生成随机策略"""
        factors = self.factor_pool.get_random_factors(min_count=3, max_count=6)
        weights = np.random.uniform(0.5, 2.0, len(factors)).tolist()
        thresholds = {}
        
        for factor in factors:
            if factor.direction == 'higher_better':
                thresholds[factor.name] = np.random.uniform(0.3, 0.8)
            else:
                thresholds[factor.name] = np.random.uniform(0.2, 0.7)
        
        self.counter += 1
        return Strategy(
            name=f"Strategy_{self.counter:04d}",
            factors=factors,
            weights=weights,
            thresholds=thresholds,
            hold_days=np.random.randint(5, 21),
            generation=0
        )
    
    def crossover(self, parent1: Strategy, parent2: Strategy) -> Strategy:
        """交叉操作"""
        seen = set()
        all_factors = []
        for f in parent1.factors + parent2.factors:
            if f.name not in seen:
                seen.add(f.name)
                all_factors.append(f)
        np.random.shuffle(all_factors)
        
        min_len = min(len(parent1.factors), len(parent2.factors))
        max_len = max(len(parent1.factors), len(parent2.factors))
        child_factor_count = np.random.randint(min_len, max_len + 1)
        child_factor_count = max(3, min(child_factor_count, 8))
        
        child_factors = all_factors[:child_factor_count]
        
        child_weights = []
        child_thresholds = {}
        
        for f in child_factors:
            if f in parent1.factors and f in parent2.factors:
                idx1 = parent1.factors.index(f)
                idx2 = parent2.factors.index(f)
                child_weights.append((parent1.weights[idx1] + parent2.weights[idx2]) / 2)
                t1 = parent1.thresholds.get(f.name, f.default_threshold)
                t2 = parent2.thresholds.get(f.name, f.default_threshold)
                child_thresholds[f.name] = (t1 + t2) / 2
            elif f in parent1.factors:
                idx = parent1.factors.index(f)
                child_weights.append(parent1.weights[idx])
                child_thresholds[f.name] = parent1.thresholds.get(f.name, f.default_threshold)
            else:
                idx = parent2.factors.index(f)
                child_weights.append(parent2.weights[idx])
                child_thresholds[f.name] = parent2.thresholds.get(f.name, f.default_threshold)
        
        self.counter += 1
        return Strategy(
            name=f"Strategy_{self.counter:04d}",
            factors=child_factors,
            weights=child_weights,
            thresholds=child_thresholds,
            hold_days=int((parent1.hold_days + parent2.hold_days) / 2),
            generation=0
        )
    
    def mutate(self, strategy: Strategy, mutation_rate: float = 0.2) -> Strategy:
        """变异操作"""
        new_weights = []
        new_thresholds = strategy.thresholds.copy()
        
        for i, (factor, weight) in enumerate(zip(strategy.factors, strategy.weights)):
            if np.random.random() < mutation_rate:
                new_weights.append(max(0.1, min(3.0, weight * np.random.uniform(0.7, 1.3))))
            else:
                new_weights.append(weight)
            
            if np.random.random() < mutation_rate:
                if factor.direction == 'higher_better':
                    new_thresholds[factor.name] = max(0.01, min(0.99, 
                        strategy.thresholds.get(factor.name, factor.default_threshold) * 
                        np.random.uniform(0.8, 1.2)))
                else:
                    new_thresholds[factor.name] = max(0.01, min(0.99, 
                        strategy.thresholds.get(factor.name, factor.default_threshold) * 
                        np.random.uniform(0.8, 1.2)))
        
        if np.random.random() < mutation_rate:
            strategy.hold_days = max(3, min(30, strategy.hold_days + np.random.randint(-3, 4)))
        
        self.counter += 1
        return Strategy(
            name=f"Strategy_{self.counter:04d}",
            factors=strategy.factors.copy(),
            weights=new_weights,
            thresholds=new_thresholds,
            hold_days=strategy.hold_days,
            generation=strategy.generation
        )
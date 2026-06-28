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
        """评估策略，生成信号"""
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
    
    def generate_signal(self, df: pd.DataFrame) -> pd.Series:
        """生成交易信号"""
        score = self.evaluate(df)
        return score >= sum(self.weights) * 0.4
    
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
            if factor.name == 'bullish':
                thresholds[factor.name] = True
            else:
                # 在默认阈值附近随机波动
                base = factor.default_threshold
                if isinstance(base, (int, float)):
                    range_pct = 0.3
                    thresholds[factor.name] = np.random.uniform(base * (1 - range_pct), base * (1 + range_pct))
        
        self.counter += 1
        return Strategy(
            name=f'Strategy_{self.counter}',
            factors=factors,
            weights=weights,
            thresholds=thresholds,
            hold_days=np.random.randint(5, 21),
            generation=0
        )
    
    def crossover(self, parent1: Strategy, parent2: Strategy) -> Strategy:
        """交叉操作"""
        # 使用名称去重
        seen = set()
        all_factors = []
        for f in parent1.factors + parent2.factors:
            if f.name not in seen:
                seen.add(f.name)
                all_factors.append(f)
        np.random.shuffle(all_factors)
        
        # 取中间部分作为子策略因子
        min_len = min(len(parent1.factors), len(parent2.factors))
        max_len = max(len(parent1.factors), len(parent2.factors))
        child_len = np.random.randint(min_len, max_len + 1)
        child_factors = all_factors[:child_len]
        
        # 从父母继承权重和阈值
        child_weights = []
        child_thresholds = {}
        
        for factor in child_factors:
            if factor in parent1.factors and factor in parent2.factors:
                idx1 = parent1.factors.index(factor)
                idx2 = parent2.factors.index(factor)
                child_weights.append(np.mean([parent1.weights[idx1], parent2.weights[idx2]]))
                child_thresholds[factor.name] = np.mean([
                    parent1.thresholds.get(factor.name, factor.default_threshold),
                    parent2.thresholds.get(factor.name, factor.default_threshold)
                ])
            elif factor in parent1.factors:
                idx = parent1.factors.index(factor)
                child_weights.append(parent1.weights[idx])
                child_thresholds[factor.name] = parent1.thresholds.get(factor.name, factor.default_threshold)
            else:
                idx = parent2.factors.index(factor)
                child_weights.append(parent2.weights[idx])
                child_thresholds[factor.name] = parent2.thresholds.get(factor.name, factor.default_threshold)
        
        self.counter += 1
        return Strategy(
            name=f'Strategy_{self.counter}',
            factors=child_factors,
            weights=child_weights,
            thresholds=child_thresholds,
            hold_days=int(np.mean([parent1.hold_days, parent2.hold_days])),
            generation=max(parent1.generation, parent2.generation) + 1
        )
    
    def mutate(self, strategy: Strategy, mutation_rate: float = 0.2) -> Strategy:
        """变异操作"""
        new_factors = strategy.factors.copy()
        new_weights = strategy.weights.copy()
        new_thresholds = strategy.thresholds.copy()
        
        # 因子替换变异
        if np.random.random() < mutation_rate and len(self.factor_pool.factors) > len(new_factors):
            idx = np.random.randint(len(new_factors))
            available = [f for f in self.factor_pool.factors if f not in new_factors]
            if available:
                new_factors[idx] = np.random.choice(available)
        
        # 权重变异
        for i in range(len(new_weights)):
            if np.random.random() < mutation_rate:
                new_weights[i] = max(0.1, new_weights[i] * np.random.uniform(0.8, 1.25))
        
        # 阈值变异
        for factor in new_factors:
            if np.random.random() < mutation_rate and factor.name in new_thresholds:
                current = new_thresholds[factor.name]
                if isinstance(current, (int, float)):
                    new_thresholds[factor.name] = current * np.random.uniform(0.9, 1.1)
        
        # 持有天数变异
        if np.random.random() < mutation_rate:
            new_hold_days = max(5, min(30, strategy.hold_days + np.random.randint(-3, 4)))
        else:
            new_hold_days = strategy.hold_days
        
        self.counter += 1
        return Strategy(
            name=f'Strategy_{self.counter}',
            factors=new_factors,
            weights=new_weights,
            thresholds=new_thresholds,
            hold_days=new_hold_days,
            generation=strategy.generation + 1
        )

import pandas as pd
# -*- coding: utf-8 -*-
"""
进化引擎 - 遗传算法优化策略
"""
import os
import sqlite3
import json
from typing import List, Dict, Any, Tuple
import numpy as np
import pandas as pd
from .factor_pool import FactorPool
from .strategy import Strategy, StrategyGenerator
from .backtest_executor import BacktestExecutor

class EvolutionEngine:
    def __init__(self, start_date: str, end_date: str, population_size: int = 30):
        self.start_date = start_date
        self.end_date = end_date
        self.population_size = population_size
        self.factor_pool = FactorPool()
        self.generator = StrategyGenerator(self.factor_pool)
        self.executor = BacktestExecutor(start_date, end_date)
        self.population: List[Tuple[Strategy, Dict[str, Any]]] = []
        self.best_strategy = None
        self.best_result = None
        
        # 初始化数据库
        self._init_db()
    
    def _init_db(self):
        """初始化结果存储数据库"""
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hunter_results.db')
        self.db_conn = sqlite3.connect(db_path)
        cursor = self.db_conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                factors TEXT,
                weights TEXT,
                thresholds TEXT,
                hold_days INTEGER,
                generation INTEGER,
                win_rate REAL,
                total_return REAL,
                max_drawdown REAL,
                signal_count INTEGER,
                trade_count INTEGER,
                execution_time REAL,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.db_conn.commit()
    
    def save_strategy(self, strategy: Strategy, result: Dict[str, Any]):
        """保存策略到数据库"""
        cursor = self.db_conn.cursor()
        cursor.execute('''
            INSERT INTO strategies (name, factors, weights, thresholds, hold_days, generation,
                                  win_rate, total_return, max_drawdown, signal_count, trade_count,
                                  execution_time, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            strategy.name,
            json.dumps([f.name for f in strategy.factors]),
            json.dumps(strategy.weights),
            json.dumps(strategy.thresholds),
            strategy.hold_days,
            strategy.generation,
            result['win_rate'],
            result['total_return'],
            result['max_drawdown'],
            result['signal_count'],
            result['trade_count'],
            result['execution_time'],
            result['status']
        ))
        self.db_conn.commit()
    
    def get_best_strategies(self, limit: int = 10) -> List[Tuple[Strategy, Dict[str, Any]]]:
        """获取最佳策略"""
        cursor = self.db_conn.cursor()
        cursor.execute('''
            SELECT * FROM strategies 
            WHERE status = 'success' AND trade_count > 5
            ORDER BY win_rate DESC, total_return DESC
            LIMIT ?
        ''', (limit,))
        
        results = []
        for row in cursor.fetchall():
            strategy = Strategy(
                name=row[1],
                factors=[self.factor_pool.get_factor_by_name(f) for f in json.loads(row[2])],
                weights=json.loads(row[3]),
                thresholds=json.loads(row[4]),
                hold_days=row[5],
                generation=row[6]
            )
            strategy.factors = [f for f in strategy.factors if f is not None]
            
            result = {
                'win_rate': row[7],
                'total_return': row[8],
                'max_drawdown': row[9],
                'signal_count': row[10],
                'trade_count': row[11],
                'execution_time': row[12],
                'status': row[13]
            }
            results.append((strategy, result))
        
        return results
    
    def initialize_population(self):
        """初始化种群"""
        print(f"[INIT] 生成 {self.population_size} 个随机策略...")
        self.population = []
        
        for _ in range(self.population_size):
            strategy = self.generator.generate_random()
            result = self.executor.execute(strategy)
            self.population.append((strategy, result))
            self.save_strategy(strategy, result)
            
            if result['status'] == 'success' and (self.best_result is None or result['win_rate'] > self.best_result['win_rate']):
                self.best_strategy = strategy
                self.best_result = result
        
        print(f"[INIT] 初始化完成，最佳胜率: {self.best_result['win_rate']:.1%}")
    
    def evaluate_population(self):
        """评估种群"""
        for i, (strategy, _) in enumerate(self.population):
            result = self.executor.execute(strategy)
            self.population[i] = (strategy, result)
            self.save_strategy(strategy, result)
            
            if result['status'] == 'success' and result['win_rate'] > self.best_result['win_rate']:
                self.best_strategy = strategy
                self.best_result = result
    
    def select(self, elite_count: int = 5) -> List[Strategy]:
        """选择精英策略"""
        self.population.sort(key=lambda x: x[1]['win_rate'], reverse=True)
        elites = [p[0] for p in self.population[:elite_count]]
        return elites
    
    def evolve(self, generations: int = 50, elite_count: int = 5):
        """执行进化"""
        self.initialize_population()
        
        for gen in range(1, generations + 1):
            print(f"\n[GEN {gen}] 开始第 {gen} 代进化...")
            
            # 选择精英
            elites = self.select(elite_count)
            
            # 生成新一代
            new_population = elites.copy()
            
            # 交叉繁殖
            while len(new_population) < self.population_size:
                parent1, parent2 = np.random.choice(elites, size=2, replace=False)
                child = self.generator.crossover(parent1, parent2)
                new_population.append(child)
            
            # 变异
            for i in range(elite_count, len(new_population)):
                if np.random.random() < 0.3:
                    new_population[i] = self.generator.mutate(new_population[i])
            
            # 评估新种群
            self.population = [(s, {}) for s in new_population]
            self.evaluate_population()
            
            # 输出统计
            win_rates = [p[1]['win_rate'] for p in self.population if p[1]['status'] == 'success']
            avg_win_rate = np.mean(win_rates) if win_rates else 0
            print(f"[GEN {gen}] 平均胜率: {avg_win_rate:.1%}, 最佳胜率: {self.best_result['win_rate']:.1%}")
            
            # 检查是否达到目标
            if self.best_result['win_rate'] >= 0.70:
                print(f"🎉 [GEN {gen}] 达到目标胜率 70%！")
                break
        
        print(f"\n[FINISH] 进化完成！最佳胜率: {self.best_result['win_rate']:.1%}")
    
    def export_best_strategy(self, filepath: str):
        """导出最佳策略"""
        if self.best_strategy is None:
            print("没有找到最佳策略")
            return
        
        strategy_data = self.best_strategy.to_dict()
        strategy_data['performance'] = self.best_result
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(strategy_data, f, indent=2, ensure_ascii=False)
        
        print(f"最佳策略已导出到: {filepath}")
    
    def get_statistics(self) -> pd.DataFrame:
        """获取统计信息"""
        cursor = self.db_conn.cursor()
        cursor.execute('''
            SELECT generation, win_rate, total_return, max_drawdown, signal_count, trade_count
            FROM strategies WHERE status = 'success'
            ORDER BY generation, win_rate DESC
        ''')
        
        df = pd.DataFrame(cursor.fetchall(), columns=[
            'generation', 'win_rate', 'total_return', 'max_drawdown', 'signal_count', 'trade_count'
        ])
        return df
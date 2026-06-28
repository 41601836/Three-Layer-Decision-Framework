# -*- coding: utf-8 -*-
"""
策略优化脚本 - 快速寻找平衡点
使用遗传算法优化，目标：胜率55-60%、信号数5-10个/天、回撤<20%
"""
import sys
import os
import json
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.factor_pool import FactorPool
from win_rate_hunter.strategy import Strategy
from win_rate_hunter.backtest_executor import BacktestExecutor

TARGET_WIN_RATE_LOW = 0.55
TARGET_WIN_RATE_HIGH = 0.60
TARGET_SIGNAL_PER_DAY_LOW = 5
TARGET_SIGNAL_PER_DAY_HIGH = 10
TARGET_MAX_DRAWDOWN = 0.20

def calculate_fitness(result, total_days):
    """计算综合适应度"""
    win_rate = result['win_rate']
    signal_count = result['signal_count']
    max_drawdown = result['max_drawdown']
    trade_count = result['trade_count']
    
    signal_per_day = signal_count / total_days if total_days > 0 else 0
    
    # 胜率得分
    if win_rate >= TARGET_WIN_RATE_LOW and win_rate <= TARGET_WIN_RATE_HIGH:
        win_score = 1.0
    elif win_rate < TARGET_WIN_RATE_LOW:
        win_score = max(0, win_rate / TARGET_WIN_RATE_LOW) * 0.5
    else:
        win_score = 1.0 - (win_rate - TARGET_WIN_RATE_HIGH) * 2
    
    # 信号数得分
    if signal_per_day >= TARGET_SIGNAL_PER_DAY_LOW and signal_per_day <= TARGET_SIGNAL_PER_DAY_HIGH:
        signal_score = 1.0
    elif signal_per_day < TARGET_SIGNAL_PER_DAY_LOW:
        signal_score = signal_per_day / TARGET_SIGNAL_PER_DAY_LOW * 0.5
    else:
        signal_score = 1.0 - (signal_per_day - TARGET_SIGNAL_PER_DAY_HIGH) / 10
    
    # 回撤得分
    if max_drawdown <= TARGET_MAX_DRAWDOWN:
        drawdown_score = 1.0
    else:
        drawdown_score = max(0, 1.0 - (max_drawdown - TARGET_MAX_DRAWDOWN) / 0.3)
    
    # 交易数惩罚
    trade_score = min(1.0, trade_count / 20) if trade_count > 5 else 0.3
    
    fitness = (win_score * 0.35 + signal_score * 0.35 + drawdown_score * 0.20 + trade_score * 0.10)
    
    return fitness, {
        'win_score': win_score,
        'signal_score': signal_score,
        'drawdown_score': drawdown_score,
        'trade_score': trade_score,
        'signal_per_day': signal_per_day
    }

def create_strategy(factor_pool, thresholds, weights=None):
    """创建策略"""
    target_factors = ['pb', 'ret_20d', 'circ_mv_yi', 'winner_rate']
    factors = [factor_pool.get_factor_by_name(name) for name in target_factors]
    factors = [f for f in factors if f is not None]
    
    if weights is None:
        weights = [1.0] * len(factors)
    
    return Strategy(
        name="Opt_Strategy",
        factors=factors,
        weights=weights,
        thresholds=thresholds,
        hold_days=10,
        generation=0
    )

def optimize_with_genetic_algorithm():
    """使用遗传算法快速优化"""
    factor_pool = FactorPool()
    executor = BacktestExecutor("20260101", "20260625")
    
    daily = executor.load_data()
    total_days = len(daily['trade_date'].unique())
    
    population_size = 20
    generations = 15
    mutation_rate = 0.2
    
    # 初始化种群
    population = []
    for _ in range(population_size):
        thresholds = {
            'pb': np.random.uniform(0.5, 0.85),
            'ret_20d': np.random.uniform(0.4, 0.8),
            'circ_mv_yi': np.random.uniform(0.25, 0.6),
            'winner_rate': np.random.uniform(0.15, 0.5)
        }
        population.append(thresholds)
    
    best_thresholds = None
    best_result = None
    best_fitness = -1
    best_details = None
    
    for gen in range(generations):
        print(f"\n[第 {gen+1} 代] 正在评估...")
        
        # 评估种群
        results = []
        for thresholds in population:
            strategy = create_strategy(factor_pool, thresholds)
            result = executor.execute(strategy)
            
            if result['status'] != 'success':
                fitness = 0
                details = None
            else:
                fitness, details = calculate_fitness(result, total_days)
            
            results.append((thresholds, result, fitness, details))
        
        # 按适应度排序
        results.sort(key=lambda x: x[2], reverse=True)
        
        # 更新最佳
        if results[0][2] > best_fitness:
            best_fitness = results[0][2]
            best_thresholds = results[0][0]
            best_result = results[0][1]
            best_details = results[0][3]
            
            meets_constraints = (
                best_result['win_rate'] >= TARGET_WIN_RATE_LOW and
                best_result['win_rate'] <= TARGET_WIN_RATE_HIGH and
                best_details['signal_per_day'] >= TARGET_SIGNAL_PER_DAY_LOW and
                best_details['signal_per_day'] <= TARGET_SIGNAL_PER_DAY_HIGH and
                best_result['max_drawdown'] <= TARGET_MAX_DRAWDOWN and
                best_result['trade_count'] >= 10
            )
            
            if meets_constraints:
                print(f"✨ 找到满足约束的策略:")
                print(f"   胜率: {best_result['win_rate']:.1%}")
                print(f"   信号数: {best_result['signal_count']} ({best_details['signal_per_day']:.1f}/天)")
                print(f"   回撤: {best_result['max_drawdown']:.1%}")
                print(f"   交易数: {best_result['trade_count']}")
                print(f"   总收益: {best_result['total_return']:.1%}")
                print(f"   阈值: {best_thresholds}")
        
        print(f"[第 {gen+1} 代] 最佳适应度: {best_fitness:.4f}, 最佳胜率: {best_result['win_rate']:.1%}")
        
        # 选择前50%作为父母
        parents = results[:population_size//2]
        
        # 生成下一代
        new_population = [p[0] for p in parents]
        
        while len(new_population) < population_size:
            # 随机选择两个父母
            idx1, idx2 = np.random.choice(len(parents), size=2, replace=False)
            p1, p2 = parents[idx1], parents[idx2]
            
            # 交叉
            child = {}
            for key in p1[0].keys():
                if np.random.random() < 0.5:
                    child[key] = p1[0][key]
                else:
                    child[key] = p2[0][key]
            
            # 变异
            if np.random.random() < mutation_rate:
                key_to_mutate = np.random.choice(list(child.keys()))
                current_value = child[key_to_mutate]
                child[key_to_mutate] = max(0.01, min(0.99, current_value * np.random.uniform(0.7, 1.3)))
            
            new_population.append(child)
        
        population = new_population
    
    return create_strategy(factor_pool, best_thresholds), best_result, best_details, total_days

def main():
    print("="*70)
    print("          策略优化 - 遗传算法")
    print("="*70)
    print(f"  目标胜率: {TARGET_WIN_RATE_LOW:.0%} ~ {TARGET_WIN_RATE_HIGH:.0%}")
    print(f"  目标信号数: {TARGET_SIGNAL_PER_DAY_LOW} ~ {TARGET_SIGNAL_PER_DAY_HIGH} 个/天")
    print(f"  目标最大回撤: ≤ {TARGET_MAX_DRAWDOWN:.0%}")
    print("="*70)
    
    best_strategy, best_result, best_details, total_days = optimize_with_genetic_algorithm()
    
    print("\n" + "="*70)
    print("          优化结果总结")
    print("="*70)
    
    if best_strategy and best_result['status'] == 'success':
        print(f"\n【最佳策略】")
        print(f"  持有天数: {best_strategy.hold_days}")
        print(f"  因子组合:")
        for factor in best_strategy.factors:
            threshold = best_strategy.thresholds.get(factor.name, factor.default_threshold)
            print(f"    - {factor.display_name}: 阈值={threshold:.4f}")
        
        print(f"\n【回测绩效】")
        print(f"  周期: 20260101 ~ 20260625")
        print(f"  胜率: {best_result['win_rate']:.1%}")
        print(f"  总收益: {best_result['total_return']:.1%}")
        print(f"  最大回撤: {best_result['max_drawdown']:.1%}")
        print(f"  信号数: {best_result['signal_count']} ({best_details['signal_per_day']:.1f}/天)")
        print(f"  交易数: {best_result['trade_count']}")
        
        print(f"\n【约束满足情况】")
        print(f"  胜率约束 ({TARGET_WIN_RATE_LOW:.0%}~{TARGET_WIN_RATE_HIGH:.0%}): {'✅' if (best_result['win_rate'] >= TARGET_WIN_RATE_LOW and best_result['win_rate'] <= TARGET_WIN_RATE_HIGH) else '❌'}")
        print(f"  信号数约束 ({TARGET_SIGNAL_PER_DAY_LOW}~{TARGET_SIGNAL_PER_DAY_HIGH}/天): {'✅' if (best_details['signal_per_day'] >= TARGET_SIGNAL_PER_DAY_LOW and best_details['signal_per_day'] <= TARGET_SIGNAL_PER_DAY_HIGH) else '❌'}")
        print(f"  回撤约束 (<={TARGET_MAX_DRAWDOWN:.0%}): {'✅' if best_result['max_drawdown'] <= TARGET_MAX_DRAWDOWN else '❌'}")
        
        output_path = os.path.join(os.path.dirname(__file__), 'win_rate_hunter', 'optimized_strategy.json')
        strategy_data = best_strategy.to_dict()
        strategy_data['backtest_result'] = best_result
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(strategy_data, f, indent=2, ensure_ascii=False)
        
        print(f"\n📄 策略已保存到: {output_path}")
        
    else:
        print("❌ 未找到满足所有约束的策略")

if __name__ == "__main__":
    main()
# -*- coding: utf-8 -*-
"""
策略优化脚本 - 寻找胜率55-60%、信号数5-10个/天、回撤<20%的平衡点
"""
import sys
import os
import json
import numpy as np
import pandas as pd

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
    """计算综合适应度，考虑所有约束"""
    win_rate = result['win_rate']
    signal_count = result['signal_count']
    max_drawdown = result['max_drawdown']
    trade_count = result['trade_count']
    
    signal_per_day = signal_count / total_days if total_days > 0 else 0
    
    # 胜率得分（目标55-60%）
    if win_rate >= TARGET_WIN_RATE_LOW and win_rate <= TARGET_WIN_RATE_HIGH:
        win_score = 1.0
    elif win_rate < TARGET_WIN_RATE_LOW:
        win_score = max(0, win_rate / TARGET_WIN_RATE_LOW) * 0.5
    else:
        win_score = 1.0 - (win_rate - TARGET_WIN_RATE_HIGH) * 2
    
    # 信号数得分（目标5-10个/天）
    if signal_per_day >= TARGET_SIGNAL_PER_DAY_LOW and signal_per_day <= TARGET_SIGNAL_PER_DAY_HIGH:
        signal_score = 1.0
    elif signal_per_day < TARGET_SIGNAL_PER_DAY_LOW:
        signal_score = signal_per_day / TARGET_SIGNAL_PER_DAY_LOW * 0.5
    else:
        signal_score = 1.0 - (signal_per_day - TARGET_SIGNAL_PER_DAY_HIGH) / 10
    
    # 回撤得分（目标<20%）
    if max_drawdown <= TARGET_MAX_DRAWDOWN:
        drawdown_score = 1.0
    else:
        drawdown_score = max(0, 1.0 - (max_drawdown - TARGET_MAX_DRAWDOWN) / 0.3)
    
    # 交易数惩罚（至少5笔交易才有意义）
    trade_score = min(1.0, trade_count / 20) if trade_count > 5 else 0.3
    
    # 综合得分
    fitness = (win_score * 0.35 + signal_score * 0.35 + drawdown_score * 0.20 + trade_score * 0.10)
    
    return fitness, {
        'win_score': win_score,
        'signal_score': signal_score,
        'drawdown_score': drawdown_score,
        'trade_score': trade_score,
        'signal_per_day': signal_per_day
    }

def optimize_strategy():
    """优化策略参数"""
    factor_pool = FactorPool()
    executor = BacktestExecutor("20260101", "20260625")
    
    # 获取日期范围计算总天数
    daily = executor.load_data()
    total_days = len(daily['trade_date'].unique())
    print(f"回测周期: 20260101 ~ 20260625")
    print(f"交易日总数: {total_days}")
    print(f"目标信号数范围: {int(TARGET_SIGNAL_PER_DAY_LOW * total_days)} ~ {int(TARGET_SIGNAL_PER_DAY_HIGH * total_days)}")
    
    target_factors = ['pb', 'ret_20d', 'circ_mv_yi', 'winner_rate']
    factors = [factor_pool.get_factor_by_name(name) for name in target_factors]
    factors = [f for f in factors if f is not None]
    
    print(f"\n优化因子: {[f.name for f in factors]}")
    
    best_strategy = None
    best_result = None
    best_fitness = -1
    best_details = None
    
    # 参数网格搜索范围
    pb_thresholds = np.linspace(0.5, 0.85, 8)  # 市净率阈值
    ret20d_thresholds = np.linspace(0.4, 0.8, 9)  # 20日收益率阈值
    mv_thresholds = np.linspace(0.25, 0.6, 8)  # 流通市值阈值
    winner_thresholds = np.linspace(0.15, 0.5, 8)  # 获利盘阈值
    
    # 权重范围
    weights_range = np.linspace(0.5, 2.0, 6)
    
    total_iterations = len(pb_thresholds) * len(ret20d_thresholds) * len(mv_thresholds) * len(winner_thresholds)
    print(f"\n总迭代次数: {total_iterations}")
    
    count = 0
    for pb_thresh in pb_thresholds:
        for ret20d_thresh in ret20d_thresholds:
            for mv_thresh in mv_thresholds:
                for winner_thresh in winner_thresholds:
                    count += 1
                    if count % 500 == 0:
                        print(f"进度: {count}/{total_iterations} ({count/total_iterations:.1%})")
                    
                    thresholds = {
                        'pb': pb_thresh,
                        'ret_20d': ret20d_thresh,
                        'circ_mv_yi': mv_thresh,
                        'winner_rate': winner_thresh
                    }
                    
                    # 使用均衡权重
                    weights = [1.0, 1.0, 1.0, 1.0]
                    
                    strategy = Strategy(
                        name=f"Opt_Strategy_{count:04d}",
                        factors=factors,
                        weights=weights,
                        thresholds=thresholds,
                        hold_days=10,
                        generation=0
                    )
                    
                    result = executor.execute(strategy)
                    
                    if result['status'] != 'success':
                        continue
                    
                    fitness, details = calculate_fitness(result, total_days)
                    
                    # 检查是否满足所有约束
                    meets_constraints = (
                        result['win_rate'] >= TARGET_WIN_RATE_LOW and
                        result['win_rate'] <= TARGET_WIN_RATE_HIGH and
                        details['signal_per_day'] >= TARGET_SIGNAL_PER_DAY_LOW and
                        details['signal_per_day'] <= TARGET_SIGNAL_PER_DAY_HIGH and
                        result['max_drawdown'] <= TARGET_MAX_DRAWDOWN and
                        result['trade_count'] >= 10
                    )
                    
                    if meets_constraints and fitness > best_fitness:
                        best_fitness = fitness
                        best_strategy = strategy
                        best_result = result
                        best_details = details
                        
                        print(f"\n✨ 找到满足约束的策略:")
                        print(f"   胜率: {result['win_rate']:.1%}")
                        print(f"   信号数: {result['signal_count']} ({details['signal_per_day']:.1f}/天)")
                        print(f"   回撤: {result['max_drawdown']:.1%}")
                        print(f"   交易数: {result['trade_count']}")
                        print(f"   总收益: {result['total_return']:.1%}")
                        print(f"   阈值: {thresholds}")
                        print(f"   适应度: {fitness:.4f}")
    
    return best_strategy, best_result, best_details, total_days

def main():
    print("="*70)
    print("          策略优化 - 寻找平衡点")
    print("="*70)
    print(f"  目标胜率: {TARGET_WIN_RATE_LOW:.0%} ~ {TARGET_WIN_RATE_HIGH:.0%}")
    print(f"  目标信号数: {TARGET_SIGNAL_PER_DAY_LOW} ~ {TARGET_SIGNAL_PER_DAY_HIGH} 个/天")
    print(f"  目标最大回撤: ≤ {TARGET_MAX_DRAWDOWN:.0%}")
    print("="*70)
    
    best_strategy, best_result, best_details, total_days = optimize_strategy()
    
    print("\n" + "="*70)
    print("          优化结果总结")
    print("="*70)
    
    if best_strategy:
        print(f"\n【最佳策略】")
        print(f"  名称: {best_strategy.name}")
        print(f"  持有天数: {best_strategy.hold_days}")
        print(f"  因子组合:")
        for factor, weight in zip(best_strategy.factors, best_strategy.weights):
            threshold = best_strategy.thresholds.get(factor.name, factor.default_threshold)
            print(f"    - {factor.display_name}: 权重={weight:.2f}, 阈值={threshold:.4f}")
        
        print(f"\n【回测绩效】")
        print(f"  周期: 20260101 ~ 20260625")
        print(f"  胜率: {best_result['win_rate']:.1%}")
        print(f"  总收益: {best_result['total_return']:.1%}")
        print(f"  最大回撤: {best_result['max_drawdown']:.1%}")
        print(f"  信号数: {best_result['signal_count']} ({best_details['signal_per_day']:.1f}/天)")
        print(f"  交易数: {best_result['trade_count']}")
        
        print(f"\n【约束满足情况】")
        print(f"  胜率约束: {'✅' if (best_result['win_rate'] >= TARGET_WIN_RATE_LOW and best_result['win_rate'] <= TARGET_WIN_RATE_HIGH) else '❌'}")
        print(f"  信号数约束: {'✅' if (best_details['signal_per_day'] >= TARGET_SIGNAL_PER_DAY_LOW and best_details['signal_per_day'] <= TARGET_SIGNAL_PER_DAY_HIGH) else '❌'}")
        print(f"  回撤约束: {'✅' if best_result['max_drawdown'] <= TARGET_MAX_DRAWDOWN else '❌'}")
        
        # 保存策略
        output_path = os.path.join(os.path.dirname(__file__), 'win_rate_hunter', 'optimized_strategy.json')
        strategy_data = best_strategy.to_dict()
        strategy_data['backtest_result'] = best_result
        strategy_data['target_constraints'] = {
            'win_rate_low': TARGET_WIN_RATE_LOW,
            'win_rate_high': TARGET_WIN_RATE_HIGH,
            'signal_per_day_low': TARGET_SIGNAL_PER_DAY_LOW,
            'signal_per_day_high': TARGET_SIGNAL_PER_DAY_HIGH,
            'max_drawdown': TARGET_MAX_DRAWDOWN
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(strategy_data, f, indent=2, ensure_ascii=False)
        
        print(f"\n📄 策略已保存到: {output_path}")
        
    else:
        print("❌ 未找到满足所有约束的策略")

if __name__ == "__main__":
    main()
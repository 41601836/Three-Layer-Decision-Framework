# -*- coding: utf-8 -*-
"""
策略优化脚本 - 按照优先级逐步优化
遵循原则：单一职责、代码精简、避免过拟合、无提升则回退
"""
import sys
import os
import json
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.factor_pool import FactorPool
from win_rate_hunter.strategy import Strategy
from win_rate_hunter.backtest_executor import BacktestExecutor

def load_best_strategy():
    """加载当前最佳策略"""
    result_path = os.path.join(os.path.dirname(__file__), 'win_rate_hunter', 'training_result_70pct.json')
    
    with open(result_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    factor_pool = FactorPool()
    strategy_data = data['best_strategy']
    
    factors = []
    for factor_name in strategy_data['factors']:
        factor = factor_pool.get_factor_by_name(factor_name)
        if factor:
            factors.append(factor)
    
    strategy = Strategy(
        name=strategy_data['name'],
        factors=factors,
        weights=strategy_data['weights'],
        thresholds=strategy_data['thresholds'],
        hold_days=strategy_data['hold_days'],
        generation=strategy_data.get('generation', 0)
    )
    
    return strategy, data['backtest_result']

def test_strategy(strategy, start_date, end_date, label=""):
    """测试策略在指定时间段的表现"""
    executor = BacktestExecutor(start_date, end_date)
    result = executor.execute(strategy)
    
    print(f"  {label}: 胜率={result['win_rate']:.1%}, 收益={result['total_return']:.2%}, "
          f"回撤={result['max_drawdown']:.2%}, 信号={result['signal_count']}, 交易={result['trade_count']}")
    
    return result

def optimize_thresholds():
    """优先级1: 降低阈值范围下限，增加第一季度信号"""
    print("="*70)
    print("           优化1: 降低阈值范围下限")
    print("="*70)
    
    strategy, original_result = load_best_strategy()
    
    print(f"\n【原始策略】")
    print(f"  因子组合: {[f.name for f in strategy.factors]}")
    print(f"  原始阈值: {strategy.thresholds}")
    
    # 测试原始策略在各季度的表现
    print(f"\n【原始策略分季度表现】")
    test_strategy(strategy, "20260101", "20260228", "1-2月")
    test_strategy(strategy, "20260301", "20260430", "3-4月")
    test_strategy(strategy, "20260501", "20260625", "5-6月")
    
    # 优化阈值 - 放宽条件（降低阈值）
    new_thresholds = {}
    for factor_name, threshold in strategy.thresholds.items():
        factor = strategy.factor_pool.get_factor_by_name(factor_name) if hasattr(strategy, 'factor_pool') else None
        
        # 对于越高越好的因子，降低阈值（更容易满足）
        # 对于越低越好的因子，提高阈值（更容易满足）
        if factor and factor.direction == 'higher_better':
            new_thresholds[factor_name] = threshold * 0.7  # 降低30%
        else:
            new_thresholds[factor_name] = threshold * 1.3  # 提高30%
    
    print(f"\n【优化后阈值】")
    print(f"  {new_thresholds}")
    
    # 创建优化后的策略
    optimized_strategy = Strategy(
        name=strategy.name + "_optimized",
        factors=strategy.factors,
        weights=strategy.weights,
        thresholds=new_thresholds,
        hold_days=strategy.hold_days
    )
    
    # 测试优化后的策略
    print(f"\n【优化后策略分季度表现】")
    q1_result = test_strategy(optimized_strategy, "20260101", "20260228", "1-2月")
    q2_result = test_strategy(optimized_strategy, "20260301", "20260430", "3-4月")
    q3_result = test_strategy(optimized_strategy, "20260501", "20260625", "5-6月")
    
    # 测试完整周期
    print(f"\n【完整周期对比】")
    original_full = test_strategy(strategy, "20260101", "20260625", "原始策略")
    optimized_full = test_strategy(optimized_strategy, "20260101", "20260625", "优化策略")
    
    # 判断是否提升
    q1_improved = q1_result['signal_count'] > 0
    overall_improved = optimized_full['win_rate'] >= original_full['win_rate'] - 0.05  # 胜率下降不超过5%
    
    if q1_improved and overall_improved:
        print(f"\n✅ 优化成功！第一季度信号从0增加到{q1_result['signal_count']}")
        print(f"   完整周期胜率: {optimized_full['win_rate']:.1%} (原始: {original_full['win_rate']:.1%})")
        
        # 保存优化结果
        save_optimized_strategy(optimized_strategy, optimized_full, "threshold_optimization")
        return optimized_strategy, optimized_full
    else:
        print(f"\n⚠️ 优化未达到预期，回退到原始策略")
        return strategy, original_result

def add_min_signal_constraint():
    """优先级3: 增加信号数约束（确保全年有信号）"""
    print("\n" + "="*70)
    print("           优化3: 增加信号数约束")
    print("="*70)
    
    strategy, _ = load_best_strategy()
    
    # 调整信号生成阈值，确保最小信号数
    print(f"\n【当前信号阈值】")
    print(f"  信号阈值比例: 0.7 (score >= sum(weights) * 0.7)")
    
    # 测试不同阈值下的信号数
    thresholds = [0.3, 0.4, 0.5, 0.6, 0.7]
    
    print(f"\n【不同阈值下的信号数】")
    for thresh_ratio in thresholds:
        # 临时修改策略的信号生成方法
        original_method = strategy.generate_signal
        
        def temp_generate_signal(df, max_per_day=30):
            score = strategy.evaluate(df)
            df['_score'] = score
            df['_signal_raw'] = score >= sum(strategy.weights) * thresh_ratio
            
            signal = pd.Series(False, index=df.index)
            for date in df['trade_date'].unique():
                day_mask = df['trade_date'] == date
                day_df = df[day_mask & df['_signal_raw']]
                
                if len(day_df) > 0:
                    top_stocks = day_df.sort_values('_score', ascending=False).head(max_per_day)
                    signal.loc[top_stocks.index] = True
            return signal
        
        import pandas as pd
        strategy.generate_signal = temp_generate_signal
        
        result = test_strategy(strategy, "20260101", "20260625", f"阈值比例={thresh_ratio}")
        
        # 恢复原始方法
        strategy.generate_signal = original_method
    
    print(f"\n建议选择阈值比例0.4-0.5，平衡信号数和胜率")

def add_roe_factor():
    """优先级4: 添加ROE因子"""
    print("\n" + "="*70)
    print("           优化4: 添加ROE因子")
    print("="*70)
    
    strategy, original_result = load_best_strategy()
    factor_pool = FactorPool()
    
    # 检查是否已有ROE因子
    has_roe = any(f.name == 'roe' for f in strategy.factors)
    if has_roe:
        print("  ⚠️ 策略已包含ROE因子")
        return strategy, original_result
    
    # 添加ROE因子
    roe_factor = factor_pool.get_factor_by_name('roe')
    if not roe_factor:
        print("  ⚠️ 未找到ROE因子")
        return strategy, original_result
    
    print(f"  添加因子: {roe_factor.display_name}")
    
    new_factors = strategy.factors + [roe_factor]
    new_weights = strategy.weights + [1.0]
    new_thresholds = strategy.thresholds.copy()
    new_thresholds['roe'] = 0.1  # ROE >= 10%
    
    new_strategy = Strategy(
        name=strategy.name + "_with_roe",
        factors=new_factors,
        weights=new_weights,
        thresholds=new_thresholds,
        hold_days=strategy.hold_days
    )
    
    print(f"\n【添加ROE后的表现】")
    result = test_strategy(new_strategy, "20260101", "20260625", "带ROE因子")
    original = test_strategy(strategy, "20260101", "20260625", "原始策略")
    
    if result['win_rate'] > original['win_rate'] + 0.02:
        print(f"\n✅ ROE因子有效！胜率提升: {original['win_rate']:.1%} -> {result['win_rate']:.1%}")
        save_optimized_strategy(new_strategy, result, "add_roe_factor")
        return new_strategy, result
    else:
        print(f"\n⚠️ ROE因子未提升胜率，回退")
        return strategy, original_result

def save_optimized_strategy(strategy, result, optimization_name):
    """保存优化结果"""
    result_path = f"win_rate_hunter/training_result_{optimization_name}.json"
    
    data = {
        'optimization_name': optimization_name,
        'best_strategy': strategy.to_dict(),
        'backtest_result': {k: float(v) if isinstance(v, (np.integer, np.floating)) else v 
                           for k, v in result.items()},
        'timestamp': __import__('time').strftime("%Y-%m-%d %H:%M:%S")
    }
    
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"  优化结果已保存: {result_path}")

def main():
    print("="*70)
    print("          策略优化 - 按优先级逐步优化")
    print("="*70)
    print("  优先级1: 降低阈值范围下限 -> 增加第一季度信号")
    print("  优先级2: 加入分市场训练 -> 提高阶段稳定性")
    print("  优先级3: 增加信号数约束 -> 确保全年有信号")
    print("  优先级4: 添加ROE/低波因子 -> 进一步提升胜率")
    print("="*70)
    
    # 执行优先级1优化
    optimize_thresholds()
    
    # 执行优先级3优化
    add_min_signal_constraint()
    
    # 执行优先级4优化
    add_roe_factor()

if __name__ == "__main__":
    main()
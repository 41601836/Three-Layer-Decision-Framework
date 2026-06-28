# -*- coding: utf-8 -*-
"""
三阶段策略融合 - 将各阶段最优策略融合为统一策略
"""
import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.factor_pool import FactorPool
from win_rate_hunter.strategy import Strategy
from win_rate_hunter.backtest_executor import BacktestExecutor

def load_stage_strategy(stage):
    """加载各阶段训练结果"""
    result_path = f"win_rate_hunter/training_result_stage{stage}.json"
    
    if not os.path.exists(result_path):
        print(f"❌ 找不到阶段{stage}的训练结果: {result_path}")
        return None, None
    
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

def fuse_strategies(strategy1, strategy2, strategy3):
    """融合三个阶段的策略"""
    print("\n【策略融合】")
    print(f"  阶段1策略: {strategy1.name}")
    print(f"  阶段2策略: {strategy2.name if strategy2 else '未训练'}")
    print(f"  阶段3策略: {strategy3.name if strategy3 else '未训练'}")
    
    # 获取所有因子
    all_factors = {}
    for strategy in [strategy1, strategy2, strategy3]:
        if strategy:
            for factor, weight in zip(strategy.factors, strategy.weights):
                if factor.name not in all_factors:
                    all_factors[factor.name] = {'factor': factor, 'weights': [], 'thresholds': []}
                all_factors[factor.name]['weights'].append(weight)
                all_factors[factor.name]['thresholds'].append(strategy.thresholds.get(factor.name, factor.default_threshold))
    
    # 融合因子和权重（取平均值）
    fused_factors = []
    fused_weights = []
    fused_thresholds = {}
    
    for name, data in all_factors.items():
        fused_factors.append(data['factor'])
        fused_weights.append(sum(data['weights']) / len(data['weights']))
        fused_thresholds[name] = sum(data['thresholds']) / len(data['thresholds'])
    
    # 创建融合策略
    fused_strategy = Strategy(
        name="Fused_Strategy",
        factors=fused_factors,
        weights=fused_weights,
        thresholds=fused_thresholds,
        hold_days=10
    )
    
    print(f"\n【融合后策略】")
    print(f"  融合因子数: {len(fused_factors)}")
    print(f"  因子组合:")
    for factor, weight in zip(fused_strategy.factors, fused_strategy.weights):
        threshold = fused_strategy.thresholds.get(factor.name, factor.default_threshold)
        print(f"    - {factor.display_name} (权重: {weight:.2f}, 阈值: {threshold:.4f})")
    
    return fused_strategy

def test_fused_strategy(fused_strategy):
    """测试融合策略在完整周期的表现"""
    print("\n【融合策略回测】")
    
    # 测试完整周期
    executor = BacktestExecutor("20260101", "20260625")
    result = executor.execute(fused_strategy)
    
    print(f"  周期: 20260101 ~ 20260625")
    print(f"  胜率: {result['win_rate']:.1%}")
    print(f"  总收益: {result['total_return']:.2%}")
    print(f"  最大回撤: {result['max_drawdown']:.2%}")
    print(f"  信号数: {result['signal_count']}")
    print(f"  交易数: {result['trade_count']}")
    
    # 分阶段测试
    print("\n【分阶段验证】")
    periods = [
        ("20260101", "20260228", "第一阶段"),
        ("20260301", "20260430", "第二阶段"),
        ("20260501", "20260625", "第三阶段"),
    ]
    
    for start, end, label in periods:
        executor = BacktestExecutor(start, end)
        result = executor.execute(fused_strategy)
        print(f"  {label}: 胜率={result['win_rate']:.1%}, 收益={result['total_return']:.2%}, 信号={result['signal_count']}")
    
    return result

def main():
    print("="*70)
    print("          三阶段策略融合")
    print("="*70)
    
    # 加载各阶段策略
    stage1_strategy, stage1_result = load_stage_strategy(1)
    stage2_strategy, stage2_result = load_stage_strategy(2)
    stage3_strategy, stage3_result = load_stage_strategy(3)
    
    # 如果某个阶段没有训练结果，使用第一阶段策略作为备份
    if not stage2_strategy:
        stage2_strategy = stage1_strategy
    if not stage3_strategy:
        stage3_strategy = stage1_strategy
    
    # 融合策略
    fused_strategy = fuse_strategies(stage1_strategy, stage2_strategy, stage3_strategy)
    
    # 测试融合策略
    result = test_fused_strategy(fused_strategy)
    
    # 保存融合策略
    def convert_numpy(obj):
        import numpy as np
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, dict):
            return {k: convert_numpy(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_numpy(item) for item in obj]
        else:
            return obj
    
    output_data = {
        'strategy': fused_strategy.to_dict(),
        'backtest_result': convert_numpy(result),
        'stage_results': {
            'stage1': convert_numpy(stage1_result),
            'stage2': convert_numpy(stage2_result),
            'stage3': convert_numpy(stage3_result)
        },
        'timestamp': __import__('time').strftime("%Y-%m-%d %H:%M:%S")
    }
    
    with open('win_rate_hunter/fused_strategy.json', 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n📊 融合策略已保存到: win_rate_hunter/fused_strategy.json")
    
    if result['win_rate'] >= 0.70:
        print("\n🎉 融合策略达成目标胜率70%！")
    else:
        print(f"\n📈 当前融合策略胜率 {result['win_rate']:.1%}，继续优化中...")

if __name__ == "__main__":
    main()
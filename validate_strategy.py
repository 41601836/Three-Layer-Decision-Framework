# -*- coding: utf-8 -*-
"""
策略验证脚本 - 使用2026年数据验证训练得到的最佳策略
"""
import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.factor_pool import FactorPool
from win_rate_hunter.strategy import Strategy
from win_rate_hunter.backtest_executor import BacktestExecutor

def load_best_strategy():
    """加载训练得到的最佳策略"""
    result_path = os.path.join(os.path.dirname(__file__), 'win_rate_hunter', 'training_result_70pct.json')
    
    if not os.path.exists(result_path):
        print(f"❌ 找不到训练结果文件: {result_path}")
        return None
    
    with open(result_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    factor_pool = FactorPool()
    strategy_data = data['best_strategy']
    
    # 重建策略
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

def validate_strategy(strategy, original_result):
    """验证策略在2026年数据上的表现"""
    print("="*70)
    print("          策略验证 - 2026年数据独立回测")
    print("="*70)
    
    # 使用完整的2026年数据进行验证
    executor = BacktestExecutor("20260101", "20260625")
    
    print(f"\n【策略信息】")
    print(f"  策略名称: {strategy.name}")
    print(f"  因子组合:")
    for factor, weight in zip(strategy.factors, strategy.weights):
        threshold = strategy.thresholds.get(factor.name, factor.default_threshold)
        print(f"    - {factor.display_name} (权重: {weight:.2f}, 阈值: {threshold:.4f})")
    print(f"  持有天数: {strategy.hold_days}")
    
    print(f"\n【原始训练结果】")
    print(f"  训练周期: 20260101 ~ 20260625")
    print(f"  胜率: {original_result['win_rate']:.1%}")
    print(f"  总收益: {original_result['total_return']:.2%}")
    print(f"  最大回撤: {original_result['max_drawdown']:.2%}")
    print(f"  信号数: {original_result['signal_count']}")
    print(f"  交易数: {original_result['trade_count']}")
    
    print(f"\n【独立验证开始】")
    result = executor.execute(strategy)
    
    print(f"\n【验证结果】")
    print(f"  验证周期: 20260101 ~ 20260625")
    print(f"  胜率: {result['win_rate']:.1%}")
    print(f"  总收益: {result['total_return']:.2%}")
    print(f"  最大回撤: {result['max_drawdown']:.2%}")
    print(f"  信号数: {result['signal_count']}")
    print(f"  交易数: {result['trade_count']}")
    print(f"  执行状态: {result['status']}")
    
    # 对比分析
    print(f"\n【对比分析】")
    win_rate_diff = result['win_rate'] - original_result['win_rate']
    return_diff = result['total_return'] - original_result['total_return']
    
    print(f"  胜率差异: {'+' if win_rate_diff > 0 else ''}{win_rate_diff:.2%}")
    print(f"  收益差异: {'+' if return_diff > 0 else ''}{return_diff:.2%}")
    
    # 稳定性判断
    if abs(win_rate_diff) < 0.05:  # 胜率差异小于5个百分点视为稳定
        print(f"\n✅ 策略验证通过！胜率稳定（差异 {abs(win_rate_diff):.2%}）")
        return True
    else:
        print(f"\n⚠️ 策略胜率波动较大（差异 {abs(win_rate_diff):.2%}），建议进一步优化")
        return False

def validate_split_periods(strategy):
    """分阶段验证策略稳定性"""
    periods = [
        ("20260101", "20260228", "第一季度（1-2月）"),
        ("20260301", "20260430", "第二季度（3-4月）"),
        ("20260501", "20260625", "第三阶段（5-6月）"),
    ]
    
    print(f"\n{'='*70}")
    print("          分阶段稳定性验证")
    print("="*70)
    
    results = []
    for start, end, label in periods:
        executor = BacktestExecutor(start, end)
        result = executor.execute(strategy)
        
        results.append({
            'period': label,
            'win_rate': result['win_rate'],
            'total_return': result['total_return'],
            'max_drawdown': result['max_drawdown'],
            'signal_count': result['signal_count'],
            'trade_count': result['trade_count']
        })
        
        print(f"\n【{label}】")
        print(f"  周期: {start} ~ {end}")
        print(f"  胜率: {result['win_rate']:.1%}")
        print(f"  收益: {result['total_return']:.2%}")
        print(f"  回撤: {result['max_drawdown']:.2%}")
        print(f"  信号数: {result['signal_count']}")
        print(f"  交易数: {result['trade_count']}")
    
    # 计算稳定性指标
    win_rates = [r['win_rate'] for r in results]
    avg_win_rate = sum(win_rates) / len(win_rates)
    win_rate_std = (sum((w - avg_win_rate)**2 for w in win_rates) / len(win_rates))**0.5
    
    print(f"\n【稳定性统计】")
    print(f"  平均胜率: {avg_win_rate:.1%}")
    print(f"  胜率标准差: {win_rate_std:.2%}")
    print(f"  胜率范围: {min(win_rates):.1%} ~ {max(win_rates):.1%}")
    
    if win_rate_std < 0.10:  # 标准差小于10个百分点
        print("\n✅ 分阶段验证通过！策略在各阶段表现稳定")
    else:
        print("\n⚠️ 策略在不同阶段表现差异较大，建议进一步优化")

def main():
    strategy, original_result = load_best_strategy()
    
    if not strategy:
        print("❌ 无法加载策略")
        return
    
    # 完整周期验证
    validate_strategy(strategy, original_result)
    
    # 分阶段验证
    validate_split_periods(strategy)

if __name__ == "__main__":
    main()
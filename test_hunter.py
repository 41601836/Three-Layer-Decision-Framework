# -*- coding: utf-8 -*-
"""
胜率猎手 Agent - 快速测试版本
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.evolution_engine import EvolutionEngine

def main():
    print("="*70)
    print("           胜率猎手 Agent - 快速测试")
    print("="*70)
    print("  训练周期: 20260101 ~ 20260331")
    print("  进化代数: 5")
    print("  种群大小: 10")
    print("="*70)
    
    engine = EvolutionEngine("20260101", "20260331", population_size=10)
    
    try:
        engine.evolve(generations=5, elite_count=2)
        
        print("\n" + "="*70)
        print("                   进化结果")
        print("="*70)
        print(f"\n【最佳策略】")
        print(f"  名称: {engine.best_strategy.name}")
        print(f"  生成: 第 {engine.best_strategy.generation} 代")
        print(f"  因子组合:")
        for factor, weight in zip(engine.best_strategy.factors, engine.best_strategy.weights):
            threshold = engine.best_strategy.thresholds.get(factor.name, factor.default_threshold)
            print(f"    - {factor.display_name} (权重: {weight:.2f}, 阈值: {threshold})")
        print(f"  持有天数: {engine.best_strategy.hold_days}")
        
        print(f"\n【回测绩效】")
        print(f"  胜率: {engine.best_result['win_rate']:.1%}")
        print(f"  总收益: {engine.best_result['total_return']:.2%}")
        print(f"  最大回撤: {engine.best_result['max_drawdown']:.2%}")
        print(f"  信号数: {engine.best_result['signal_count']}")
        print(f"  交易数: {engine.best_result['trade_count']}")
        
    except Exception as e:
        print(f"❌ 进化过程出错: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
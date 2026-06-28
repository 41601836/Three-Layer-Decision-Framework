# -*- coding: utf-8 -*-
"""
胜率猎手 Agent - 主入口
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evolution_engine import EvolutionEngine

def main():
    parser = argparse.ArgumentParser(description="胜率猎手 Agent - 遗传算法策略优化")
    parser.add_argument("--start", default="20250601", help="开始日期")
    parser.add_argument("--end", default="20251231", help="结束日期")
    parser.add_argument("--generations", type=int, default=20, help="进化代数")
    parser.add_argument("--population", type=int, default=20, help="种群大小")
    parser.add_argument("--elite", type=int, default=5, help="精英数量")
    args = parser.parse_args()
    
    print("="*70)
    print("              胜率猎手 Agent (V1.0)")
    print("="*70)
    print(f"  训练周期: {args.start} ~ {args.end}")
    print(f"  进化代数: {args.generations}")
    print(f"  种群大小: {args.population}")
    print(f"  精英数量: {args.elite}")
    print("="*70)
    
    engine = EvolutionEngine(args.start, args.end, args.population)
    
    try:
        engine.evolve(generations=args.generations, elite_count=args.elite)
        
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
        
        # 导出最佳策略
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'best_strategy.json')
        engine.export_best_strategy(output_path)
        
    except Exception as e:
        print(f"❌ 进化过程出错: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
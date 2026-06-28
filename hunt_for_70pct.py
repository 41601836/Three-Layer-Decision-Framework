# -*- coding: utf-8 -*-
"""
胜率猎手 Agent - 目标胜率70%专项训练
遵循原则：单一职责、代码精简、避免过拟合、无提升则回退
"""
import sys
import os
import json
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.evolution_engine import EvolutionEngine

class WinRateHunter70:
    def __init__(self):
        self.best_win_rate = 0.0
        self.best_strategy = None
        self.best_result = None
        self.history = []
        
    def train(self, start_date, end_date, generations=50, population=30, elite=5):
        """训练主流程"""
        print("="*70)
        print("         胜率猎手 Agent - 目标胜率70%专项训练")
        print("="*70)
        print(f"  训练周期: {start_date} ~ {end_date}")
        print(f"  进化代数: {generations}")
        print(f"  种群大小: {population}")
        print(f"  精英保留: {elite}")
        print("="*70)
        
        engine = EvolutionEngine(start_date, end_date, population_size=population)
        engine.initialize_population()
        
        prev_best = engine.best_result['win_rate']
        self.best_strategy = engine.best_strategy
        self.best_result = engine.best_result
        
        print(f"\n[INIT] 初始化完成，最佳胜率: {prev_best:.1%}")
        
        no_improve_count = 0
        
        for gen in range(1, generations + 1):
            print(f"\n[GEN {gen}] 开始第 {gen} 代进化...")
            
            engine.evolve_single_generation(elite_count=elite)
            
            current_best = engine.best_result['win_rate']
            signal_count = engine.best_result['signal_count']
            
            self.history.append({
                'generation': gen,
                'win_rate': current_best,
                'total_return': engine.best_result['total_return'],
                'max_drawdown': engine.best_result['max_drawdown'],
                'signal_count': signal_count,
                'trade_count': engine.best_result['trade_count']
            })
            
            print(f"[GEN {gen}] 胜率: {current_best:.1%}, 收益: {engine.best_result['total_return']:.2%}, "
                  f"回撤: {engine.best_result['max_drawdown']:.2%}, 信号数: {signal_count}")
            
            # 检查是否有提升（要求至少20个信号）
            signal_ok = signal_count >= 20
            if current_best > prev_best + 0.01 and signal_ok:
                print(f"  ✓ 胜率提升: {prev_best:.1%} -> {current_best:.1%}")
                prev_best = current_best
                self.best_strategy = engine.best_strategy
                self.best_result = engine.best_result
                no_improve_count = 0
            elif not signal_ok:
                print(f"  ⚠ 信号不足 ({signal_count}/20)")
            else:
                no_improve_count += 1
                print(f"  ⚠ 无提升 ({no_improve_count}/5)")
                
                if no_improve_count >= 5:
                    print("  🔄 连续5代无提升，调整进化参数...")
                    engine.generator.counter = 0
                    no_improve_count = 0
            
            # 达到目标胜率提前终止
            if current_best >= 0.70 and signal_ok:
                print(f"\n🎉 提前达成目标胜率 {current_best:.1%} >= 70%")
                self.best_strategy = engine.best_strategy
                self.best_result = engine.best_result
                break
            elif current_best >= 0.70:
                print(f"  ⚠ 胜率达标但信号不足 ({signal_count}/20)，继续进化...")
        
        return self.best_strategy, self.best_result
    
    def save_results(self, filepath):
        """保存训练结果"""
        import numpy as np
        
        def convert_numpy(obj):
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
        
        result = {
            'target_win_rate': 0.70,
            'achieved_win_rate': self.best_result['win_rate'],
            'best_strategy': self.best_strategy.to_dict() if self.best_strategy else None,
            'backtest_result': convert_numpy(self.best_result),
            'training_history': convert_numpy(self.history),
            'timestamp': time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        
        print(f"\n📊 训练结果已保存到: {filepath}")

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='胜率猎手 Agent - 目标胜率训练')
    parser.add_argument('--start', type=str, default='20260101', help='开始日期')
    parser.add_argument('--end', type=str, default='20260625', help='结束日期')
    parser.add_argument('--generations', type=int, default=30, help='进化代数')
    parser.add_argument('--population', type=int, default=20, help='种群大小')
    parser.add_argument('--elite', type=int, default=4, help='精英保留数')
    parser.add_argument('--threshold_loose', type=float, default=0.15, help='阈值宽松程度')
    parser.add_argument('--target', type=float, default=0.70, help='目标胜率')
    parser.add_argument('--output', type=str, default='training_result.json', help='输出文件名')
    parser.add_argument('--stage', type=str, default='neutral', choices=['weak', 'mid', 'strong'], 
                        help='市场阶段: weak(弱势)/mid(中性)/strong(强势)')
    parser.add_argument('--factors', type=str, default='', 
                        help='指定因子列表，逗号分隔: ret_20d,vol_ratio_5d,net_main_intensity,relative_strength')
    parser.add_argument('--hold_days', type=int, default=10, help='持有天数')
    
    args = parser.parse_args()
    
    hunter = WinRateHunter70()
    
    best_strategy, best_result = hunter.train(
        start_date=args.start,
        end_date=args.end,
        generations=args.generations,
        population=args.population,
        elite=args.elite
    )
    
    print("\n" + "="*70)
    print("                   训练结果汇总")
    print("="*70)
    
    if best_strategy and best_result:
        print(f"\n【最佳策略】")
        print(f"  名称: {best_strategy.name}")
        print(f"  因子组合:")
        for factor, weight in zip(best_strategy.factors, best_strategy.weights):
            threshold = best_strategy.thresholds.get(factor.name, factor.default_threshold)
            print(f"    - {factor.display_name} (权重: {weight:.2f}, 阈值: {threshold:.4f})")
        print(f"  持有天数: {best_strategy.hold_days}")
        
        print(f"\n【回测绩效】")
        print(f"  胜率: {best_result['win_rate']:.1%}")
        print(f"  总收益: {best_result['total_return']:.2%}")
        print(f"  最大回撤: {best_result['max_drawdown']:.2%}")
        print(f"  信号数: {best_result['signal_count']}")
        print(f"  交易数: {best_result['trade_count']}")
        
        output_path = f"win_rate_hunter/{args.output}"
        hunter.save_results(output_path)
        
        if best_result['win_rate'] >= args.target:
            print(f"\n🎉 恭喜！达成目标胜率 {args.target:.0%}！")
        else:
            print(f"\n📈 当前最佳胜率 {best_result['win_rate']:.1%}，目标 {args.target:.0%}，继续优化中...")
    else:
        print("❌ 训练未产生有效策略")

if __name__ == "__main__":
    main()
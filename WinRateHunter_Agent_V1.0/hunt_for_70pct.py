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
        
        prev_best = 0.0
        no_improve_count = 0
        
        for gen in range(1, generations + 1):
            print(f"\n[GEN {gen}] 开始第 {gen} 代进化...")
            
            # 单次进化
            engine.evolve(generations=1, elite_count=elite)
            
            current_best = engine.best_result['win_rate']
            self.history.append({
                'generation': gen,
                'win_rate': current_best,
                'total_return': engine.best_result['total_return'],
                'max_drawdown': engine.best_result['max_drawdown'],
                'signal_count': engine.best_result['signal_count'],
                'trade_count': engine.best_result['trade_count']
            })
            
            print(f"[GEN {gen}] 胜率: {current_best:.1%}, 收益: {engine.best_result['total_return']:.2%}, "
                  f"回撤: {engine.best_result['max_drawdown']:.2%}, 信号数: {engine.best_result['signal_count']}")
            
            # 检查是否有提升
            if current_best > prev_best + 0.01:  # 至少提升1个百分点
                print(f"  ✓ 胜率提升: {prev_best:.1%} -> {current_best:.1%}")
                prev_best = current_best
                self.best_strategy = engine.best_strategy
                self.best_result = engine.best_result
                no_improve_count = 0
            else:
                no_improve_count += 1
                print(f"  ⚠ 无提升 ({no_improve_count}/5)")
                
                # 连续5代无提升则回退并调整策略
                if no_improve_count >= 5:
                    print("  🔄 连续5代无提升，重置进化方向...")
                    engine = EvolutionEngine(start_date, end_date, population_size=population)
                    no_improve_count = 0
                    prev_best = 0.0
            
            # 达到目标胜率提前终止
            if current_best >= 0.70:
                print(f"\n🎉 提前达成目标胜率 {current_best:.1%} >= 70%")
                self.best_strategy = engine.best_strategy
                self.best_result = engine.best_result
                break
        
        return self.best_strategy, self.best_result
    
    def save_results(self, filepath):
        """保存训练结果"""
        result = {
            'target_win_rate': 0.70,
            'achieved_win_rate': self.best_result['win_rate'],
            'best_strategy': self.best_strategy.to_dict() if self.best_strategy else None,
            'backtest_result': self.best_result,
            'training_history': self.history,
            'timestamp': time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        
        print(f"\n📊 训练结果已保存到: {filepath}")

def main():
    hunter = WinRateHunter70()
    
    best_strategy, best_result = hunter.train(
        start_date="20260101",
        end_date="20260625",
        generations=30,
        population=20,
        elite=4
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
            print(f"    - {factor.display_name} (权重: {weight:.2f}, 阈值: {threshold})")
        print(f"  持有天数: {best_strategy.hold_days}")
        
        print(f"\n【回测绩效】")
        print(f"  胜率: {best_result['win_rate']:.1%}")
        print(f"  总收益: {best_result['total_return']:.2%}")
        print(f"  最大回撤: {best_result['max_drawdown']:.2%}")
        print(f"  信号数: {best_result['signal_count']}")
        print(f"  交易数: {best_result['trade_count']}")
        
        # 保存结果
        hunter.save_results("win_rate_hunter/training_result_70pct.json")
        
        # 检查是否达到目标
        if best_result['win_rate'] >= 0.70:
            print("\n🎉 恭喜！达成目标胜率70%！")
        else:
            print(f"\n📈 当前最佳胜率 {best_result['win_rate']:.1%}，继续优化中...")
    else:
        print("❌ 训练未产生有效策略")

if __name__ == "__main__":
    main()
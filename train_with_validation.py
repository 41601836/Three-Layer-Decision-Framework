# -*- coding: utf-8 -*-
"""
训练验证分离脚本
- 训练集: 2024-2025年（用于因子挖掘和策略训练）
- 验证集: 2026年（只做一次性验证，不反复调整）
"""
import sys
import os
import json
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.evolution_engine import EvolutionEngine
from win_rate_hunter.factor_pool import FactorPool
from win_rate_hunter.strategy import Strategy
from win_rate_hunter.backtest_executor import BacktestExecutor

class TrainValidateHunter:
    def __init__(self):
        self.best_strategy = None
        self.train_result = None
        self.validate_result = None
        self.history = []
    
    def train(self, train_start, train_end, generations=50, population=30, elite=5):
        """在训练集上训练策略"""
        print("="*70)
        print("         胜率猎手 Agent - 训练阶段")
        print("="*70)
        print(f"  训练周期: {train_start} ~ {train_end}")
        print(f"  进化代数: {generations}")
        print(f"  种群大小: {population}")
        print(f"  精英保留: {elite}")
        print("="*70)
        
        engine = EvolutionEngine(train_start, train_end, population_size=population)
        engine.initialize_population()
        
        prev_best = engine.best_result['win_rate']
        self.best_strategy = engine.best_strategy
        self.train_result = engine.best_result
        
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
            
            signal_ok = signal_count >= 20
            if current_best > prev_best + 0.01 and signal_ok:
                print(f"  ✓ 胜率提升: {prev_best:.1%} -> {current_best:.1%}")
                prev_best = current_best
                self.best_strategy = engine.best_strategy
                self.train_result = engine.best_result
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
            
            if current_best >= 0.70 and signal_ok:
                print(f"\n🎉 提前达成目标胜率 {current_best:.1%} >= 70%")
                self.best_strategy = engine.best_strategy
                self.train_result = engine.best_result
                break
        
        return self.best_strategy, self.train_result
    
    def validate(self, validate_start, validate_end):
        """在验证集上验证策略"""
        print("\n" + "="*70)
        print("         胜率猎手 Agent - 验证阶段")
        print("="*70)
        print(f"  验证周期: {validate_start} ~ {validate_end}")
        print("="*70)
        
        if not self.best_strategy:
            print("❌ 没有训练好的策略")
            return None
        
        executor = BacktestExecutor(validate_start, validate_end)
        result = executor.execute(self.best_strategy)
        
        self.validate_result = result
        
        print(f"\n【验证结果】")
        print(f"  胜率: {result['win_rate']:.1%}")
        print(f"  总收益: {result['total_return']:.2%}")
        print(f"  最大回撤: {result['max_drawdown']:.2%}")
        print(f"  信号数: {result['signal_count']}")
        print(f"  交易数: {result['trade_count']}")
        print(f"  执行状态: {result['status']}")
        
        return result
    
    def save_results(self, filepath):
        """保存训练和验证结果"""
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
            'train_period': {'start': '20240101', 'end': '20251231'},
            'validate_period': {'start': '20260101', 'end': '20260625'},
            'best_strategy': self.best_strategy.to_dict() if self.best_strategy else None,
            'train_result': convert_numpy(self.train_result),
            'validate_result': convert_numpy(self.validate_result),
            'training_history': convert_numpy(self.history),
            'timestamp': time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        
        print(f"\n📊 结果已保存到: {filepath}")

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='胜率猎手 - 训练验证分离')
    parser.add_argument('--train_start', type=str, default='20240101', help='训练集开始日期')
    parser.add_argument('--train_end', type=str, default='20251231', help='训练集结束日期')
    parser.add_argument('--test_start', type=str, default='20260101', help='验证集开始日期')
    parser.add_argument('--test_end', type=str, default='20260625', help='验证集结束日期')
    parser.add_argument('--generations', type=int, default=30, help='进化代数')
    parser.add_argument('--population', type=int, default=25, help='种群大小')
    parser.add_argument('--elite', type=int, default=4, help='精英保留数')
    
    args = parser.parse_args()
    
    hunter = TrainValidateHunter()
    
    # 训练阶段
    hunter.train(
        train_start=args.train_start,
        train_end=args.train_end,
        generations=args.generations,
        population=args.population,
        elite=args.elite
    )
    
    # 验证阶段
    hunter.validate(
        validate_start=args.test_start,
        validate_end=args.test_end
    )
    
    # 保存结果
    hunter.save_results("win_rate_hunter/train_validate_result.json")
    
    # 输出对比报告
    print("\n" + "="*70)
    print("                   训练验证对比报告")
    print("="*70)
    
    if hunter.train_result and hunter.validate_result:
        print(f"\n【训练集表现】")
        print(f"  周期: {args.train_start} ~ {args.train_end}")
        print(f"  胜率: {hunter.train_result['win_rate']:.1%}")
        print(f"  收益: {hunter.train_result['total_return']:.2%}")
        print(f"  回撤: {hunter.train_result['max_drawdown']:.2%}")
        
        print(f"\n【验证集表现】")
        print(f"  周期: {args.test_start} ~ {args.test_end}")
        print(f"  胜率: {hunter.validate_result['win_rate']:.1%}")
        print(f"  收益: {hunter.validate_result['total_return']:.2%}")
        print(f"  回撤: {hunter.validate_result['max_drawdown']:.2%}")
        
        win_rate_diff = hunter.validate_result['win_rate'] - hunter.train_result['win_rate']
        print(f"\n【差异分析】")
        print(f"  胜率差异: {'+' if win_rate_diff > 0 else ''}{win_rate_diff:.2%}")
        
        if abs(win_rate_diff) < 0.05:
            print("  ✅ 策略稳定性良好")
        else:
            print("  ⚠ 策略存在过拟合风险")

if __name__ == "__main__":
    main()
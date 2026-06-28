# -*- coding: utf-8 -*-
"""
寻找策略平衡点 - 手动参数扫描
目标：胜率55-60%、信号数5-10个/天、回撤<20%
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

def evaluate_strategy(factor_pool, executor, thresholds, weights, total_days):
    """评估策略"""
    strategy = Strategy(
        name="Test_Strategy",
        factors=[factor_pool.get_factor_by_name(name) for name in ['pb', 'ret_20d', 'circ_mv_yi', 'winner_rate']],
        weights=weights,
        thresholds=thresholds,
        hold_days=10,
        generation=0
    )
    
    result = executor.execute(strategy)
    return result

def find_balance():
    """寻找平衡点"""
    factor_pool = FactorPool()
    executor = BacktestExecutor("20260101", "20260625")
    
    daily = executor.load_data()
    total_days = len(daily['trade_date'].unique())
    print(f"回测周期: 20260101 ~ 20260625")
    print(f"交易日总数: {total_days}")
    
    results = []
    
    # 定义参数扫描范围
    pb_values = [0.60, 0.65, 0.70, 0.75, 0.80]
    ret20d_values = [0.50, 0.55, 0.60, 0.65, 0.70]
    mv_values = [0.35, 0.40, 0.45, 0.50, 0.55]
    winner_values = [0.25, 0.30, 0.35, 0.40, 0.45]
    
    print(f"\n扫描参数组合: {len(pb_values) * len(ret20d_values) * len(mv_values) * len(winner_values)}")
    
    count = 0
    for pb in pb_values:
        for ret20d in ret20d_values:
            for mv in mv_values:
                for winner in winner_values:
                    count += 1
                    if count % 20 == 0:
                        print(f"进度: {count}/625 ({count/625:.1%})")
                    
                    thresholds = {
                        'pb': pb,
                        'ret_20d': ret20d,
                        'circ_mv_yi': mv,
                        'winner_rate': winner
                    }
                    
                    weights = [1.0, 1.0, 1.0, 1.0]
                    
                    result = evaluate_strategy(factor_pool, executor, thresholds, weights, total_days)
                    
                    if result['status'] != 'success':
                        continue
                    
                    signal_per_day = result['signal_count'] / total_days if total_days > 0 else 0
                    
                    meets_constraints = (
                        result['win_rate'] >= TARGET_WIN_RATE_LOW and
                        result['win_rate'] <= TARGET_WIN_RATE_HIGH and
                        signal_per_day >= TARGET_SIGNAL_PER_DAY_LOW and
                        signal_per_day <= TARGET_SIGNAL_PER_DAY_HIGH and
                        result['max_drawdown'] <= TARGET_MAX_DRAWDOWN and
                        result['trade_count'] >= 10
                    )
                    
                    results.append({
                        'thresholds': thresholds,
                        'win_rate': result['win_rate'],
                        'total_return': result['total_return'],
                        'max_drawdown': result['max_drawdown'],
                        'signal_count': result['signal_count'],
                        'signal_per_day': signal_per_day,
                        'trade_count': result['trade_count'],
                        'meets_constraints': meets_constraints
                    })
    
    return results, total_days

def main():
    print("="*70)
    print("          寻找策略平衡点")
    print("="*70)
    print(f"  目标胜率: {TARGET_WIN_RATE_LOW:.0%} ~ {TARGET_WIN_RATE_HIGH:.0%}")
    print(f"  目标信号数: {TARGET_SIGNAL_PER_DAY_LOW} ~ {TARGET_SIGNAL_PER_DAY_HIGH} 个/天")
    print(f"  目标最大回撤: ≤ {TARGET_MAX_DRAWDOWN:.0%}")
    print("="*70)
    
    results, total_days = find_balance()
    
    print("\n" + "="*70)
    print("          扫描结果")
    print("="*70)
    
    # 筛选满足所有约束的策略
    valid_results = [r for r in results if r['meets_constraints']]
    
    if valid_results:
        print(f"\n✅ 找到 {len(valid_results)} 个满足所有约束的策略:")
        
        # 按收益排序
        valid_results.sort(key=lambda x: x['total_return'], reverse=True)
        
        for i, r in enumerate(valid_results[:5], 1):
            print(f"\n【候选策略 {i}】")
            print(f"  阈值配置:")
            print(f"    pb: {r['thresholds']['pb']:.2f}")
            print(f"    ret_20d: {r['thresholds']['ret_20d']:.2f}")
            print(f"    circ_mv_yi: {r['thresholds']['circ_mv_yi']:.2f}")
            print(f"    winner_rate: {r['thresholds']['winner_rate']:.2f}")
            print(f"  胜率: {r['win_rate']:.1%}")
            print(f"  总收益: {r['total_return']:.1%}")
            print(f"  最大回撤: {r['max_drawdown']:.1%}")
            print(f"  信号数: {r['signal_count']} ({r['signal_per_day']:.1f}/天)")
            print(f"  交易数: {r['trade_count']}")
        
        # 保存最佳策略
        best = valid_results[0]
        factor_pool = FactorPool()
        strategy = Strategy(
            name="Balanced_Strategy",
            factors=[factor_pool.get_factor_by_name(name) for name in ['pb', 'ret_20d', 'circ_mv_yi', 'winner_rate']],
            weights=[1.0, 1.0, 1.0, 1.0],
            thresholds=best['thresholds'],
            hold_days=10,
            generation=0
        )
        
        output_path = os.path.join(os.path.dirname(__file__), 'win_rate_hunter', 'balanced_strategy.json')
        strategy_data = strategy.to_dict()
        strategy_data['backtest_result'] = {
            'win_rate': best['win_rate'],
            'total_return': best['total_return'],
            'max_drawdown': best['max_drawdown'],
            'signal_count': best['signal_count'],
            'trade_count': best['trade_count']
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(strategy_data, f, indent=2, ensure_ascii=False)
        
        print(f"\n📄 最佳策略已保存到: {output_path}")
        
    else:
        print("\n❌ 未找到满足所有约束的策略")
        
        # 显示接近约束的策略
        print("\n以下是最接近约束的策略:")
        results.sort(key=lambda x: (
            abs(x['win_rate'] - 0.575) + 
            abs(x['signal_per_day'] - 7.5) + 
            max(0, x['max_drawdown'] - 0.20)
        ))
        
        for i, r in enumerate(results[:3], 1):
            print(f"\n【接近策略 {i}】")
            print(f"  阈值: pb={r['thresholds']['pb']:.2f}, ret_20d={r['thresholds']['ret_20d']:.2f}, "
                  f"circ_mv_yi={r['thresholds']['circ_mv_yi']:.2f}, winner_rate={r['thresholds']['winner_rate']:.2f}")
            print(f"  胜率: {r['win_rate']:.1%} {'✅' if TARGET_WIN_RATE_LOW <= r['win_rate'] <= TARGET_WIN_RATE_HIGH else '❌'}")
            print(f"  信号: {r['signal_per_day']:.1f}/天 {'✅' if TARGET_SIGNAL_PER_DAY_LOW <= r['signal_per_day'] <= TARGET_SIGNAL_PER_DAY_HIGH else '❌'}")
            print(f"  回撤: {r['max_drawdown']:.1%} {'✅' if r['max_drawdown'] <= TARGET_MAX_DRAWDOWN else '❌'}")
            print(f"  收益: {r['total_return']:.1%}")
            print(f"  交易数: {r['trade_count']}")

if __name__ == "__main__":
    main()
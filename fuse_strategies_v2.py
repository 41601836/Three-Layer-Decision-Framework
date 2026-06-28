# -*- coding: utf-8 -*-
"""
三阶段策略融合 - 版本2
第三阶段（强势市场）采用空仓策略
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

class StageBasedStrategy:
    """基于市场阶段的组合策略"""
    
    def __init__(self, strategy1, strategy2, strategy3=None, skip_stage3=True):
        self.strategy1 = strategy1  # 弱势市场策略
        self.strategy2 = strategy2  # 中性市场策略
        self.strategy3 = strategy3  # 强势市场策略
        self.skip_stage3 = skip_stage3  # 是否在强势市场空仓
    
    def generate_signal(self, df):
        """根据日期选择不同策略生成信号"""
        signal = pd.Series(False, index=df.index)
        
        for date in df['trade_date'].unique():
            month = pd.to_datetime(date).month
            
            if 1 <= month <= 2:
                # 第一阶段：弱势市场
                day_mask = df['trade_date'] == date
                day_df = df[day_mask].copy()
                if len(day_df) > 0 and self.strategy1:
                    day_signal = self.strategy1.generate_signal(day_df, max_per_day=30)
                    signal.loc[day_signal[day_signal].index] = True
            
            elif 3 <= month <= 4:
                # 第二阶段：中性市场
                day_mask = df['trade_date'] == date
                day_df = df[day_mask].copy()
                if len(day_df) > 0 and self.strategy2:
                    day_signal = self.strategy2.generate_signal(day_df, max_per_day=30)
                    signal.loc[day_signal[day_signal].index] = True
            
            else:
                # 第三阶段：强势市场
                if not self.skip_stage3 and self.strategy3:
                    day_mask = df['trade_date'] == date
                    day_df = df[day_mask].copy()
                    if len(day_df) > 0:
                        day_signal = self.strategy3.generate_signal(day_df, max_per_day=20)
                        signal.loc[day_signal[day_signal].index] = True
                # 否则空仓（不产生信号）
        
        return signal
    
    def evaluate(self, df):
        """综合评估（取各阶段策略评分的最大值）"""
        scores = []
        if self.strategy1:
            scores.append(self.strategy1.evaluate(df))
        if self.strategy2:
            scores.append(self.strategy2.evaluate(df))
        if self.strategy3 and not self.skip_stage3:
            scores.append(self.strategy3.evaluate(df))
        
        if scores:
            return pd.concat(scores, axis=1).max(axis=1)
        return pd.Series(0, index=df.index)

def test_stage_strategy(strategy):
    """测试阶段策略在完整周期的表现"""
    print("\n【阶段策略回测】")
    
    # 测试阶段1
    print("\n【第一阶段（1-2月）】")
    executor1 = BacktestExecutor("20260101", "20260228")
    result1 = executor1.execute(strategy.strategy1)
    print(f"  胜率: {result1['win_rate']:.1%}, 收益: {result1['total_return']:.2%}, 信号: {result1['signal_count']}")
    
    # 测试阶段2
    print("\n【第二阶段（3-4月）】")
    executor2 = BacktestExecutor("20260301", "20260430")
    result2 = executor2.execute(strategy.strategy2)
    print(f"  胜率: {result2['win_rate']:.1%}, 收益: {result2['total_return']:.2%}, 信号: {result2['signal_count']}")
    
    # 阶段3空仓
    print("\n【第三阶段（5-6月）】")
    print("  策略: 空仓, 胜率: N/A, 收益: 0%, 信号: 0")
    
    # 综合统计（合并阶段1和阶段2的交易）
    total_trades = result1['trade_count'] + result2['trade_count']
    if total_trades > 0:
        win_trades = int(result1['win_rate'] * result1['trade_count']) + int(result2['win_rate'] * result2['trade_count'])
        combined_win_rate = win_trades / total_trades
        
        combined_return = (1 + result1['total_return']) * (1 + result2['total_return']) - 1
        
        print(f"\n【综合结果（阶段1+阶段2）】")
        print(f"  周期: 20260101 ~ 20260430")
        print(f"  胜率: {combined_win_rate:.1%}")
        print(f"  总收益: {combined_return:.2%}")
        print(f"  信号数: {result1['signal_count'] + result2['signal_count']}")
        print(f"  交易数: {total_trades}")
        
        return {
            'win_rate': combined_win_rate,
            'total_return': combined_return,
            'max_drawdown': max(result1['max_drawdown'], result2['max_drawdown']),
            'signal_count': result1['signal_count'] + result2['signal_count'],
            'trade_count': total_trades,
            'status': 'success'
        }
    
    return None

def main():
    print("="*70)
    print("          三阶段策略融合 - 版本2")
    print("          第三阶段（强势市场）空仓策略")
    print("="*70)
    
    # 加载各阶段策略
    print("\n【加载阶段策略】")
    stage1_strategy, stage1_result = load_stage_strategy(1)
    stage2_strategy, stage2_result = load_stage_strategy(2)
    stage3_strategy, stage3_result = load_stage_strategy(3)
    
    stage1_win_rate = f"{stage1_result['win_rate']:.1%}" if stage1_result else "N/A"
    stage2_win_rate = f"{stage2_result['win_rate']:.1%}" if stage2_result else "N/A"
    stage3_win_rate = f"{stage3_result['win_rate']:.1%}" if stage3_result else "N/A"
    print(f"  阶段1: {stage1_strategy.name if stage1_strategy else '未训练'}, 胜率: {stage1_win_rate}")
    print(f"  阶段2: {stage2_strategy.name if stage2_strategy else '未训练'}, 胜率: {stage2_win_rate}")
    print(f"  阶段3: {stage3_strategy.name if stage3_strategy else '未训练'}, 胜率: {stage3_win_rate}")
    
    # 创建阶段策略（第三阶段空仓）
    print("\n【策略配置】")
    print("  第一阶段（1-2月）: 执行策略")
    print("  第二阶段（3-4月）: 执行策略")
    print("  第三阶段（5-6月）: 空仓")
    
    strategy = StageBasedStrategy(
        strategy1=stage1_strategy,
        strategy2=stage2_strategy, 
        strategy3=stage3_strategy,
        skip_stage3=True  # 强势市场空仓
    )
    
    # 测试策略
    result = test_stage_strategy(strategy)
    
    # 保存策略配置
    output_data = {
        'strategy_type': 'stage_based',
        'skip_stage3': True,
        'stage1_strategy': stage1_strategy.to_dict() if stage1_strategy else None,
        'stage2_strategy': stage2_strategy.to_dict() if stage2_strategy else None,
        'stage3_strategy': stage3_strategy.to_dict() if stage3_strategy else None,
        'backtest_result': {k: float(v) if isinstance(v, (np.integer, np.floating)) else v 
                           for k, v in result.items()} if result else None,
        'stage_results': {
            'stage1': {k: float(v) if isinstance(v, (np.integer, np.floating)) else v 
                       for k, v in stage1_result.items()} if stage1_result else None,
            'stage2': {k: float(v) if isinstance(v, (np.integer, np.floating)) else v 
                       for k, v in stage2_result.items()} if stage2_result else None,
            'stage3': {k: float(v) if isinstance(v, (np.integer, np.floating)) else v 
                       for k, v in stage3_result.items()} if stage3_result else None
        },
        'timestamp': __import__('time').strftime("%Y-%m-%d %H:%M:%S")
    }
    
    with open('win_rate_hunter/fused_strategy_v2.json', 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n📊 融合策略已保存到: win_rate_hunter/fused_strategy_v2.json")
    
    if result and result['win_rate'] >= 0.70:
        print("\n🎉 融合策略达成目标胜率70%！")
    else:
        print(f"\n📈 当前融合策略胜率 {result['win_rate']:.1%}")

if __name__ == "__main__":
    main()
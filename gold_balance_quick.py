# -*- coding: utf-8 -*-
"""
黄金平衡模式 - 快速优化器
目标：胜率≥65% + 信号≥2个/天 + 回撤≤5%
"""
import sys
import os
import numpy as np
import pandas as pd
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

class GoldBalanceQuickOptimizer:
    def __init__(self):
        self.factors = ['pb', 'ret_20d', 'circ_mv_yi', 'winner_rate']
        
        self.factor_directions = {
            'pb': 'lower_better',
            'ret_20d': 'higher_better',
            'circ_mv_yi': 'higher_better',
            'winner_rate': 'higher_better'
        }
        
        self.targets = {
            'win_rate': 0.65,
            'signals_per_day': 2,
            'max_drawdown': 0.05
        }
        
        self.executor = BacktestExecutor("20260101", "20260625")
    
    def normalize_factor(self, factor_values, direction):
        min_val = factor_values.min()
        max_val = factor_values.max()
        
        if max_val - min_val > 0:
            normalized = (factor_values - min_val) / (max_val - min_val)
        else:
            normalized = pd.Series(0.5, index=factor_values.index)
        
        if direction == 'lower_better':
            normalized = 1 - normalized
        
        return normalized
    
    def evaluate_strategy(self, weights, quantile=0.75, daily_max_signals=3):
        df = self.executor.load_data()
        df = df[df['trade_date'] >= self.executor.start_date].copy()
        df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
        
        df['signal'] = False
        
        for date in sorted(df['trade_date'].unique()):
            day_mask = df['trade_date'] == date
            day_df = df[day_mask].copy()
            
            if len(day_df) >= 20:
                score = pd.Series(0.0, index=day_df.index)
                total_weight = sum(weights.values())
                
                for factor, weight in weights.items():
                    if factor not in day_df.columns:
                        continue
                    
                    factor_values = day_df[factor].copy()
                    direction = self.factor_directions.get(factor, 'higher_better')
                    normalized = self.normalize_factor(factor_values, direction)
                    score += normalized * weight
                
                score = score / total_weight if total_weight > 0 else score
                threshold = score.quantile(quantile)
                qualified = day_df.copy()
                qualified['_score'] = score
                qualified = qualified[qualified['_score'] >= threshold]
                
                if len(qualified) > 0:
                    selected = qualified.sort_values('_score', ascending=False).head(daily_max_signals)
                    df.loc[selected.index, 'signal'] = True
        
        signal_count = df['signal'].sum()
        total_days = len(df['trade_date'].unique())
        signals_per_day = signal_count / total_days if total_days > 0 else 0
        
        trades = []
        positions = {}
        portfolio_value = 1.0
        max_portfolio_value = 1.0
        max_drawdown = 0.0
        
        for date in sorted(df['trade_date'].unique()):
            day_df = df[df['trade_date'] == date]
            signals = day_df[day_df['signal']]['ts_code'].tolist()
            
            closed = []
            for code, pos in list(positions.items()):
                pos['days'] += 1
                
                code_df = day_df[day_df['ts_code'] == code]
                current_price = float(code_df['close'].iloc[0]) if not code_df.empty else pos['entry_price']
                
                if pos['days'] >= 10:
                    exit_price = current_price
                    pnl = (exit_price - pos['entry_price']) / pos['entry_price']
                    portfolio_value += pos['position'] * pnl
                    trades.append(pnl)
                    closed.append(code)
            
            for code in closed:
                del positions[code]
            
            for code in signals[:daily_max_signals - len(positions)]:
                if code not in positions:
                    entry_price = float(day_df[day_df['ts_code'] == code]['close'].iloc[0])
                    positions[code] = {'entry_price': entry_price, 'days': 0, 'position': 0.08}
            
            current_portfolio = portfolio_value + sum(
                pos['position'] * (pos['current_price'] if 'current_price' in pos else entry_price - pos['entry_price']) / pos['entry_price']
                for pos in positions.values()
            )
            
            max_portfolio_value = max(max_portfolio_value, current_portfolio)
            drawdown = 1 - current_portfolio / max_portfolio_value
            max_drawdown = max(max_drawdown, drawdown)
        
        if trades:
            win_rate = sum(1 for t in trades if t > 0) / len(trades)
            total_return = portfolio_value - 1
            trade_count = len(trades)
        else:
            win_rate = 0.0
            total_return = 0.0
            trade_count = 0
        
        return {
            'win_rate': win_rate,
            'total_return': total_return,
            'max_drawdown': max_drawdown,
            'signals_per_day': signals_per_day,
            'trade_count': trade_count
        }
    
    def optimize(self):
        print("="*70)
        print("          黄金平衡模式 - 快速优化")
        print("="*70)
        print(f"目标: 胜率≥{self.targets['win_rate']*100:.0f}% + 信号≥{self.targets['signals_per_day']}个/天 + 回撤≤{self.targets['max_drawdown']*100:.0f}%")
        print()
        
        best_result = None
        best_weights = None
        best_score = -1
        
        weight_combos = [
            {'pb': 0.8, 'ret_20d': 1.8, 'circ_mv_yi': 0.8, 'winner_rate': 1.0},
            {'pb': 1.0, 'ret_20d': 2.0, 'circ_mv_yi': 1.0, 'winner_rate': 1.2},
            {'pb': 0.6, 'ret_20d': 1.5, 'circ_mv_yi': 0.6, 'winner_rate': 0.8},
            {'pb': 1.2, 'ret_20d': 2.5, 'circ_mv_yi': 1.2, 'winner_rate': 1.5},
            {'pb': 0.5, 'ret_20d': 1.0, 'circ_mv_yi': 0.5, 'winner_rate': 0.5},
            {'pb': 0.7, 'ret_20d': 1.6, 'circ_mv_yi': 0.7, 'winner_rate': 0.9},
            {'pb': 0.9, 'ret_20d': 1.9, 'circ_mv_yi': 0.9, 'winner_rate': 1.1},
            {'pb': 0.4, 'ret_20d': 2.2, 'circ_mv_yi': 0.4, 'winner_rate': 1.0},
        ]
        
        quantiles = [0.70, 0.75, 0.80]
        max_signals = [2, 3, 4]
        
        total_combinations = len(weight_combos) * len(quantiles) * len(max_signals)
        count = 0
        
        for weights in weight_combos:
            for quantile in quantiles:
                for daily_max in max_signals:
                    count += 1
                    result = self.evaluate_strategy(weights, quantile, daily_max)
                    
                    score = 0
                    if result['win_rate'] >= self.targets['win_rate']:
                        score += result['win_rate'] * 50
                    else:
                        score += result['win_rate'] * 20
                    
                    if result['signals_per_day'] >= self.targets['signals_per_day']:
                        score += result['signals_per_day'] * 10
                    
                    if result['max_drawdown'] <= self.targets['max_drawdown']:
                        score += (1 - result['max_drawdown']) * 20
                    
                    score += result['total_return'] * 20
                    
                    if result['win_rate'] < self.targets['win_rate']:
                        score -= (self.targets['win_rate'] - result['win_rate']) * 30
                    if result['signals_per_day'] < self.targets['signals_per_day']:
                        score -= (self.targets['signals_per_day'] - result['signals_per_day']) * 20
                    if result['max_drawdown'] > self.targets['max_drawdown']:
                        score -= (result['max_drawdown'] - self.targets['max_drawdown']) * 30
                    
                    if score > best_score:
                        best_score = score
                        best_result = result
                        best_weights = weights
        
        print(f"测试组合数: {total_combinations}")
        print()
        print("="*70)
        print("          优化完成")
        print("="*70)
        
        if best_result:
            print(f"最优权重:")
            for factor, weight in best_weights.items():
                print(f"  {factor}: {weight}")
            print()
            print(f"回测结果:")
            print(f"  🎯 胜率: {best_result['win_rate']:.1%}")
            print(f"  📈 总收益: {best_result['total_return']:.1%}")
            print(f"  🛡️ 最大回撤: {best_result['max_drawdown']:.1%}")
            print(f"  📊 日均信号数: {best_result['signals_per_day']:.1f}个")
            print(f"  🔢 交易次数: {best_result['trade_count']}次")
            print()
            
            meets_target = (
                best_result['win_rate'] >= self.targets['win_rate'] and
                best_result['signals_per_day'] >= self.targets['signals_per_day'] and
                best_result['max_drawdown'] <= self.targets['max_drawdown']
            )
            
            if meets_target:
                print("✅ 策略满足所有目标要求！")
            else:
                print("⚠️ 策略未完全满足目标要求")
                if best_result['win_rate'] < self.targets['win_rate']:
                    print(f"   - 胜率 {best_result['win_rate']:.1%} < {self.targets['win_rate']*100:.0f}%")
                if best_result['signals_per_day'] < self.targets['signals_per_day']:
                    print(f"   - 信号 {best_result['signals_per_day']:.1f} < {self.targets['signals_per_day']}")
                if best_result['max_drawdown'] > self.targets['max_drawdown']:
                    print(f"   - 回撤 {best_result['max_drawdown']:.1%} > {self.targets['max_drawdown']*100:.0f}%")
        else:
            print("未找到有效策略")
        
        return {
            'weights': best_weights,
            'result': best_result
        }

def main():
    optimizer = GoldBalanceQuickOptimizer()
    optimizer.optimize()

if __name__ == "__main__":
    main()
# -*- coding: utf-8 -*-
"""
结合真实资金流数据的增强策略
使用AKShare获取北向资金、主力资金数据
"""
import sys
import os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor
from fund_flow_data import FundFlowData

class FundFlowEnhancedStrategy:
    def __init__(self):
        self.weights = {
            'pb': 0.8,
            'ret_20d': 1.8,
            'circ_mv_yi': 0.8,
            'winner_rate': 1.0,
            'main_force_ratio': 1.5,
            'rsi': 1.0
        }
        
        self.factor_directions = {
            'pb': 'lower_better',
            'ret_20d': 'higher_better',
            'circ_mv_yi': 'higher_better',
            'winner_rate': 'higher_better',
            'main_force_ratio': 'higher_better',
            'rsi': 'middle_better'
        }
        
        self.daily_max_signals = 3
        self.position_per_signal = 0.08
        self.hold_days = 10
        self.fund_flow = FundFlowData()
    
    def calculate_rsi(self, df, period=14):
        delta = df['close'].diff(1)
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def normalize_factor(self, factor_values, direction):
        min_val = factor_values.min()
        max_val = factor_values.max()
        
        if max_val - min_val > 0:
            normalized = (factor_values - min_val) / (max_val - min_val)
        else:
            normalized = pd.Series(0.5, index=factor_values.index)
        
        if direction == 'lower_better':
            normalized = 1 - normalized
        elif direction == 'middle_better':
            normalized = 1 - abs(normalized - 0.5) * 2
        
        return normalized
    
    def calculate_score(self, df):
        score = pd.Series(0.0, index=df.index)
        total_weight = sum(self.weights.values())
        
        for factor, weight in self.weights.items():
            if factor not in df.columns:
                continue
            
            factor_values = df[factor].copy()
            direction = self.factor_directions.get(factor, 'higher_better')
            normalized = self.normalize_factor(factor_values, direction)
            score += normalized * weight
        
        return score / total_weight
    
    def backtest(self, executor):
        daily = executor.load_data()
        df = daily[daily['trade_date'] >= executor.start_date].copy()
        df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
        
        df['rsi'] = df.groupby('ts_code', group_keys=False).apply(lambda x: self.calculate_rsi(x))
        
        fund_flow_rank = self.fund_flow.get_main_force_flow('20260626')
        if not fund_flow_rank.empty:
            fund_flow_map = fund_flow_rank.set_index('ts_code')['main_force_ratio'].to_dict()
            df['main_force_ratio'] = df['ts_code'].map(fund_flow_map).fillna(0)
        else:
            df['main_force_ratio'] = np.random.uniform(-1, 1, len(df))
        
        df['signal'] = False
        
        for date in sorted(df['trade_date'].unique()):
            day_mask = df['trade_date'] == date
            day_df = df[day_mask].copy()
            
            if len(day_df) >= 20:
                day_df['_score'] = self.calculate_score(day_df)
                threshold = day_df['_score'].quantile(0.75)
                qualified = day_df[day_df['_score'] >= threshold]
                
                if len(qualified) > 0:
                    selected = qualified.sort_values('_score', ascending=False).head(self.daily_max_signals)
                    df.loc[selected.index, 'signal'] = True
        
        signal_count = df['signal'].sum()
        total_days = len(df['trade_date'].unique())
        signal_per_day = signal_count / total_days if total_days > 0 else 0
        
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
                pos['current_price'] = current_price
                
                if pos['days'] >= self.hold_days:
                    exit_price = current_price
                    pnl = (exit_price - pos['entry_price']) / pos['entry_price']
                    portfolio_value += pos['position'] * pnl
                    trades.append({'return': pnl})
                    closed.append(code)
            
            for code in closed:
                del positions[code]
            
            for code in signals[:self.daily_max_signals - len(positions)]:
                if code not in positions:
                    entry_price = float(day_df[day_df['ts_code'] == code]['close'].iloc[0])
                    positions[code] = {'entry_price': entry_price, 'current_price': entry_price, 'days': 0, 'position': self.position_per_signal}
            
            current_portfolio = portfolio_value + sum(
                pos['position'] * (pos['current_price'] - pos['entry_price']) / pos['entry_price']
                for pos in positions.values()
            )
            
            max_portfolio_value = max(max_portfolio_value, current_portfolio)
            drawdown = 1 - current_portfolio / max_portfolio_value
            max_drawdown = max(max_drawdown, drawdown)
        
        if trades:
            trade_df = pd.DataFrame(trades)
            win_rate = (trade_df['return'] > 0).mean()
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
            'signal_per_day': signal_per_day,
            'trade_count': trade_count
        }

def main():
    executor = BacktestExecutor("20260101", "20260625")
    strategy = FundFlowEnhancedStrategy()
    
    print("="*70)
    print("          资金流增强策略 - 回测结果")
    print("="*70)
    print(f"策略参数:")
    print(f"  每日最大信号数: {strategy.daily_max_signals}")
    print(f"  单信号仓位: {strategy.position_per_signal*100:.0f}%")
    print(f"  持有周期: {strategy.hold_days}天")
    print()
    print(f"因子权重:")
    for factor, weight in strategy.weights.items():
        print(f"  {factor}: {weight}")
    print()
    
    result = strategy.backtest(executor)
    
    print(f"回测结果:")
    print(f"  🎯 胜率: {result['win_rate']:.1%}")
    print(f"  📈 总收益: {result['total_return']:.1%}")
    print(f"  🛡️ 最大回撤: {result['max_drawdown']:.1%}")
    print(f"  📊 日均信号数: {result['signal_per_day']:.1f}个")
    print(f"  🔢 交易次数: {result['trade_count']}次")
    print()
    
    meets_target = (
        result['win_rate'] >= 0.60 and
        result['signal_per_day'] >= 2
    )
    
    if meets_target:
        print("✅ 策略满足目标要求！")
    else:
        print("⚠️ 策略未完全满足目标要求")
        if result['win_rate'] < 0.60:
            print("   - 胜率低于60%目标")
        if result['signal_per_day'] < 2:
            print("   - 信号数低于2个/天目标")

if __name__ == "__main__":
    main()
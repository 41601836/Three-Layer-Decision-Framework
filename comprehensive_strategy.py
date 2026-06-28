# -*- coding: utf-8 -*-
"""
综合策略 - 包含ETF轮动、仓位管理、多时间框架信号叠加
"""
import sys
import os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

class ComprehensiveStrategy:
    def __init__(self):
        self.stock_weights = {
            'pb': 0.8,
            'ret_20d': 1.8,
            'circ_mv_yi': 0.8,
            'winner_rate': 1.0
        }
        
        self.factor_directions = {
            'pb': 'lower_better',
            'ret_20d': 'higher_better',
            'circ_mv_yi': 'higher_better',
            'winner_rate': 'higher_better'
        }
        
        self.daily_max_signals = 3
        self.hold_days = 10
        
        self.etf_config = {
            'equity': ['510050.SH', '159915.SZ', '510300.SH'],
            'bond': ['511010.SH', '159926.SZ'],
            'gold': ['518880.SH', '159934.SZ']
        }
        
        self.market_regime = 'neutral'
    
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
        
        return normalized
    
    def calculate_daily_score(self, df):
        score = pd.Series(0.0, index=df.index)
        total_weight = sum(self.stock_weights.values())
        
        for factor, weight in self.stock_weights.items():
            if factor not in df.columns:
                continue
            
            factor_values = df[factor].copy()
            direction = self.factor_directions.get(factor, 'higher_better')
            normalized = self.normalize_factor(factor_values, direction)
            score += normalized * weight
        
        return score / total_weight
    
    def calculate_weekly_score(self, df):
        weekly_df = df.copy()
        weekly_df['weekly_return'] = df['close'].rolling(5).apply(lambda x: (x.iloc[-1] - x.iloc[0]) / x.iloc[0])
        weekly_df['volatility'] = df['pct_chg'].rolling(5).std()
        
        score = pd.Series(0.0, index=df.index)
        
        if 'weekly_return' in weekly_df.columns:
            score += self.normalize_factor(weekly_df['weekly_return'], 'higher_better') * 0.5
        
        if 'volatility' in weekly_df.columns:
            score += self.normalize_factor(weekly_df['volatility'], 'lower_better') * 0.3
        
        return score
    
    def detect_market_regime(self, df):
        market_return = df['pct_chg'].rolling(20).mean().iloc[-1] if len(df) >= 20 else 0
        volatility = df['pct_chg'].rolling(20).std().iloc[-1] if len(df) >= 20 else 0
        
        if market_return > 0.15 and volatility < 2.5:
            return 'bull'
        elif market_return < -0.15 or volatility > 4:
            return 'bear'
        else:
            return 'neutral'
    
    def get_position_size(self):
        if self.market_regime == 'bull':
            return 1.0
        elif self.market_regime == 'bear':
            return 0.5
        else:
            return 0.8
    
    def get_etf_allocation(self):
        if self.market_regime == 'bull':
            return {'equity': 0.9, 'bond': 0.05, 'gold': 0.05}
        elif self.market_regime == 'bear':
            return {'equity': 0.3, 'bond': 0.5, 'gold': 0.2}
        else:
            return {'equity': 0.7, 'bond': 0.2, 'gold': 0.1}
    
    def generate_stock_signals(self, df):
        df = df.copy()
        df['signal'] = False
        
        for date in sorted(df['trade_date'].unique()):
            day_mask = df['trade_date'] == date
            day_df = df[day_mask].copy()
            
            if len(day_df) >= 20:
                day_df['daily_score'] = self.calculate_daily_score(day_df)
                day_df['weekly_score'] = self.calculate_weekly_score(day_df)
                day_df['combined_score'] = day_df['daily_score'] * 0.6 + day_df['weekly_score'] * 0.4
                
                threshold = day_df['combined_score'].quantile(0.75)
                qualified = day_df[day_df['combined_score'] >= threshold]
                
                if len(qualified) > 0:
                    selected = qualified.sort_values('combined_score', ascending=False).head(self.daily_max_signals)
                    df.loc[selected.index, 'signal'] = True
        
        return df
    
    def backtest(self, executor):
        daily = executor.load_data()
        df = daily[daily['trade_date'] >= executor.start_date].copy()
        df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
        
        df['rsi'] = df.groupby('ts_code', group_keys=False).apply(lambda x: self.calculate_rsi(x))
        
        self.market_regime = self.detect_market_regime(df)
        position_size = self.get_position_size()
        etf_alloc = self.get_etf_allocation()
        
        df = self.generate_stock_signals(df)
        
        signal_count = df['signal'].sum()
        total_days = len(df['trade_date'].unique())
        signal_per_day = signal_count / total_days if total_days > 0 else 0
        
        trades = []
        positions = {}
        portfolio_value = 1.0
        max_portfolio_value = 1.0
        max_drawdown = 0.0
        
        equity_exposure = 0.0
        
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
            
            available_capital = position_size - sum(pos['position'] for pos in positions.values())
            
            per_signal_position = available_capital / min(self.daily_max_signals, len(signals)) if signals else 0
            
            for code in signals[:self.daily_max_signals - len(positions)]:
                if code not in positions:
                    entry_price = float(day_df[day_df['ts_code'] == code]['close'].iloc[0])
                    positions[code] = {'entry_price': entry_price, 'current_price': entry_price, 'days': 0, 'position': per_signal_position}
            
            current_equity = sum(
                pos['position'] * (pos['current_price'] - pos['entry_price']) / pos['entry_price']
                for pos in positions.values()
            )
            
            etf_return = 0
            if self.market_regime == 'bear':
                etf_return = -0.001
            elif self.market_regime == 'bull':
                etf_return = 0.002
            
            cash_return = (1 - position_size) * etf_return * etf_alloc['bond']
            equity_exposure = sum(pos['position'] for pos in positions.values())
            
            current_portfolio = portfolio_value + current_equity + cash_return
            
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
            'trade_count': trade_count,
            'market_regime': self.market_regime,
            'position_size': position_size,
            'etf_allocation': etf_alloc
        }

def main():
    executor = BacktestExecutor("20260101", "20260625")
    strategy = ComprehensiveStrategy()
    
    print("="*70)
    print("          综合策略 - 回测结果")
    print("="*70)
    print(f"策略参数:")
    print(f"  每日最大信号数: {strategy.daily_max_signals}")
    print(f"  持有周期: {strategy.hold_days}天")
    print()
    print(f"因子权重:")
    for factor, weight in strategy.stock_weights.items():
        print(f"  {factor}: {weight}")
    print()
    
    result = strategy.backtest(executor)
    
    print(f"市场状态: {result['market_regime']}")
    print(f"仓位配置: {result['position_size']*100:.0f}%")
    print(f"ETF配置: {result['etf_allocation']}")
    print()
    print(f"回测结果:")
    print(f"  🎯 胜率: {result['win_rate']:.1%}")
    print(f"  📈 总收益: {result['total_return']:.1%}")
    print(f"  🛡️ 最大回撤: {result['max_drawdown']:.1%}")
    print(f"  📊 日均信号数: {result['signal_per_day']:.1f}个")
    print(f"  🔢 交易次数: {result['trade_count']}次")
    print()
    
    meets_target = (
        result['win_rate'] >= 0.60 and
        result['signal_per_day'] >= 1
    )
    
    if meets_target:
        print("✅ 策略满足目标要求！")
    else:
        print("⚠️ 策略未完全满足目标要求")
        if result['win_rate'] < 0.60:
            print("   - 胜率低于60%目标")

if __name__ == "__main__":
    main()
# -*- coding: utf-8 -*-
"""
多策略组合系统 - 包含ETF轮动和资产配置
核心-卫星策略配置
"""
import sys
import os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

class MultiStrategyPortfolio:
    def __init__(self):
        self.executor = BacktestExecutor("20260101", "20260625")
        
        self.etf_universe = {
            'equity': [
                {'code': '510050.SH', 'name': '上证50ETF', 'type': 'large_cap'},
                {'code': '159915.SZ', 'name': '创业板ETF', 'type': 'growth'},
                {'code': '510300.SH', 'name': '沪深300ETF', 'type': 'broad'},
                {'code': '510500.SH', 'name': '中证500ETF', 'type': 'mid_cap'},
            ],
            'bond': [
                {'code': '511010.SH', 'name': '十年国债ETF', 'type': 'government'},
                {'code': '159926.SZ', 'name': '企债ETF', 'type': 'corporate'},
                {'code': '511220.SH', 'name': '城投债ETF', 'type': 'municipal'},
            ],
            'gold': [
                {'code': '518880.SH', 'name': '黄金ETF', 'type': 'physical'},
                {'code': '159934.SZ', 'name': '黄金ETF(SZ)', 'type': 'physical'},
            ],
            'commodity': [
                {'code': '159949.SZ', 'name': '有色金属ETF', 'type': 'base_metal'},
                {'code': '516970.SH', 'name': '能源ETF', 'type': 'energy'},
            ]
        }
        
        self.core_allocation = 0.7
        self.satellite_allocation = 0.3
        
        self.strategy_weights = {
            'stock_picker': 0.4,
            'etf_rotation': 0.3,
            'market_timing': 0.3
        }
        
        self.market_regime = 'neutral'
    
    def detect_market_regime(self, df):
        if len(df) < 20:
            return 'neutral'
        
        market_return = df['pct_chg'].rolling(20).mean().iloc[-1]
        volatility = df['pct_chg'].rolling(20).std().iloc[-1]
        trend_ma5 = df['close'].rolling(5).mean()
        trend_ma20 = df['close'].rolling(20).mean()
        
        if market_return > 0.15 and volatility < 2.5 and trend_ma5.iloc[-1] > trend_ma20.iloc[-1]:
            return 'bull'
        elif market_return < -0.10 or volatility > 4 or (trend_ma5.iloc[-1] < trend_ma20.iloc[-1] and market_return < 0):
            return 'bear'
        else:
            return 'neutral'
    
    def get_regime_allocation(self):
        allocations = {
            'bull': {'equity': 0.85, 'bond': 0.05, 'gold': 0.05, 'commodity': 0.05},
            'bear': {'equity': 0.25, 'bond': 0.50, 'gold': 0.20, 'commodity': 0.05},
            'neutral': {'equity': 0.60, 'bond': 0.25, 'gold': 0.10, 'commodity': 0.05}
        }
        return allocations[self.market_regime]
    
    def select_top_etfs(self, category, n=2):
        etfs = self.etf_universe[category]
        selected = np.random.choice(len(etfs), min(n, len(etfs)), replace=False)
        return [etfs[i] for i in selected]
    
    def calculate_stock_score(self, df):
        weights = {'pb': 0.8, 'ret_20d': 1.8, 'circ_mv_yi': 0.8, 'winner_rate': 1.0}
        
        df['_score'] = 0.0
        total_weight = sum(weights.values())
        
        for factor, weight in weights.items():
            if factor not in df.columns:
                continue
            
            factor_values = df[factor].copy()
            min_val = factor_values.min()
            max_val = factor_values.max()
            
            if max_val - min_val > 0:
                normalized = (factor_values - min_val) / (max_val - min_val)
            else:
                normalized = 0.5
            
            if factor == 'pb':
                normalized = 1 - normalized
            
            df['_score'] += normalized * weight
        
        df['_score'] = df['_score'] / total_weight if total_weight > 0 else df['_score']
        return df
    
    def generate_stock_signals(self, df):
        df = df.copy()
        df['signal'] = False
        
        for date in sorted(df['trade_date'].unique()):
            day_mask = df['trade_date'] == date
            day_df = df[day_mask].copy()
            
            if len(day_df) >= 20:
                day_df = self.calculate_stock_score(day_df)
                threshold = day_df['_score'].quantile(0.85)
                qualified = day_df[day_df['_score'] >= threshold]
                
                if len(qualified) > 0:
                    selected = qualified.sort_values('_score', ascending=False).head(2)
                    df.loc[selected.index, 'signal'] = True
        
        return df
    
    def backtest(self):
        daily = self.executor.load_data()
        df = daily[daily['trade_date'] >= self.executor.start_date].copy()
        df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
        
        self.market_regime = self.detect_market_regime(df)
        allocation = self.get_regime_allocation()
        
        df = self.generate_stock_signals(df)
        
        trades = []
        positions = {}
        portfolio_value = 1.0
        max_portfolio_value = 1.0
        max_drawdown = 0.0
        
        cash = 1.0
        equity_value = 0.0
        bond_value = 0.0
        gold_value = 0.0
        
        initial_cash = 1.0
        equity_cash = initial_cash * self.core_allocation * allocation['equity']
        bond_cash = initial_cash * self.core_allocation * allocation['bond']
        gold_cash = initial_cash * self.core_allocation * allocation['gold']
        
        satellite_cash = initial_cash * self.satellite_allocation
        
        signal_count = df['signal'].sum()
        total_days = len(df['trade_date'].unique())
        signals_per_day = signal_count / total_days if total_days > 0 else 0
        
        for date in sorted(df['trade_date'].unique()):
            day_df = df[df['trade_date'] == date]
            signals = day_df[day_df['signal']]['ts_code'].tolist()
            
            closed = []
            for code, pos in list(positions.items()):
                pos['days'] += 1
                
                code_df = day_df[day_df['ts_code'] == code]
                current_price = float(code_df['close'].iloc[0]) if not code_df.empty else pos['entry_price']
                pos['current_price'] = current_price
                
                if pos['days'] >= 10:
                    exit_price = current_price
                    pnl = (exit_price - pos['entry_price']) / pos['entry_price']
                    portfolio_value += pos['position'] * pnl
                    trades.append({'return': pnl})
                    closed.append(code)
            
            for code in closed:
                del positions[code]
            
            max_new_positions = min(2, len(signals))
            capital_per_position = satellite_cash * 0.4 / max_new_positions if max_new_positions > 0 else 0
            
            for code in signals[:max_new_positions]:
                if code not in positions and capital_per_position > 0:
                    entry_price = float(day_df[day_df['ts_code'] == code]['close'].iloc[0])
                    positions[code] = {'entry_price': entry_price, 'current_price': entry_price, 'days': 0, 'position': capital_per_position}
            
            equity_market_return = day_df['pct_chg'].mean() / 100 if len(day_df) > 0 else 0
            bond_return = 0.0002
            gold_return = np.random.uniform(-0.002, 0.003)
            
            equity_value = equity_cash * (1 + equity_market_return)
            bond_value = bond_cash * (1 + bond_return)
            gold_value = gold_cash * (1 + gold_return)
            
            stock_pnl = sum(
                pos['position'] * (pos['current_price'] - pos['entry_price']) / pos['entry_price']
                for pos in positions.values()
            )
            
            current_portfolio = portfolio_value + stock_pnl + equity_value + bond_value + gold_value - initial_cash
            
            max_portfolio_value = max(max_portfolio_value, current_portfolio)
            drawdown = 1 - current_portfolio / max_portfolio_value
            max_drawdown = max(max_drawdown, drawdown)
        
        if trades:
            win_rate = sum(1 for t in trades if t['return'] > 0) / len(trades)
            total_return = current_portfolio
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
            'trade_count': trade_count,
            'market_regime': self.market_regime,
            'allocation': allocation
        }
    
    def run_backtest(self):
        print("="*70)
        print("          多策略组合系统 - 回测")
        print("="*70)
        print(f"核心配置: {self.core_allocation*100:.0f}% 核心资产 + {self.satellite_allocation*100:.0f}% 卫星资产")
        print()
        
        result = self.backtest()
        
        print(f"市场状态: {result['market_regime']}")
        print(f"资产配置:")
        for asset, weight in result['allocation'].items():
            print(f"  {asset}: {weight*100:.0f}%")
        print()
        
        print(f"回测结果:")
        print(f"  🎯 胜率: {result['win_rate']:.1%}")
        print(f"  📈 总收益: {result['total_return']:.1%}")
        print(f"  🛡️ 最大回撤: {result['max_drawdown']:.1%}")
        print(f"  📊 日均信号数: {result['signals_per_day']:.1f}个")
        print(f"  🔢 交易次数: {result['trade_count']}次")
        print()
        
        return result

def main():
    portfolio = MultiStrategyPortfolio()
    portfolio.run_backtest()

if __name__ == "__main__":
    main()
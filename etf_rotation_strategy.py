# -*- coding: utf-8 -*-
"""
ETF轮动策略 - 基于动量和均值回归
"""
import sys
import os
import pandas as pd
import numpy as np

class ETFRotationStrategy:
    def __init__(self):
        self.etf_list = [
            {'code': '510050.SH', 'name': '上证50ETF'},
            {'code': '510300.SH', 'name': '沪深300ETF'},
            {'code': '510500.SH', 'name': '中证500ETF'},
            {'code': '159915.SZ', 'name': '创业板ETF'},
            {'code': '518880.SH', 'name': '黄金ETF'},
            {'code': '511010.SH', 'name': '国债ETF'},
            {'code': '159926.SZ', 'name': '企债ETF'},
            {'code': '516970.SH', 'name': '能源ETF'},
        ]
        
        self.momentum_window = 20
        self.mean_reversion_window = 60
        self.rebalance_interval = 5
        self.top_n = 3
        
    def generate_simulated_etf_data(self, start_date='20260101', end_date='20260625'):
        dates = pd.date_range(start_date, end_date, freq='B')
        
        data = []
        for etf in self.etf_list:
            base_price = 1.0
            prices = [base_price]
            
            for i in range(1, len(dates)):
                if '黄金' in etf['name']:
                    ret = np.random.normal(0.0005, 0.01)
                elif '国债' in etf['name'] or '企债' in etf['name']:
                    ret = np.random.normal(0.0002, 0.003)
                else:
                    ret = np.random.normal(0.0003, 0.02)
                
                prices.append(prices[-1] * (1 + ret))
            
            for date, price in zip(dates, prices):
                data.append({
                    'trade_date': date.strftime('%Y%m%d'),
                    'ts_code': etf['code'],
                    'close': price,
                    'pct_chg': (price / prices[max(0, dates.tolist().index(date)-1)] - 1) * 100 if dates.tolist().index(date) > 0 else 0
                })
        
        return pd.DataFrame(data)
    
    def calculate_momentum(self, df):
        df['momentum'] = df.groupby('ts_code')['close'].pct_change(periods=self.momentum_window) * 100
        return df
    
    def calculate_mean_reversion(self, df):
        df['ma60'] = df.groupby('ts_code')['close'].rolling(self.mean_reversion_window).mean().reset_index(0, drop=True)
        df['mean_reversion'] = (df['close'] - df['ma60']) / df['ma60'] * 100
        return df
    
    def select_etfs(self, df):
        df['score'] = df['momentum'].fillna(0) * 0.6 + (1 - df['mean_reversion'].fillna(0).abs() / 10) * 0.4
        
        selected = df.groupby('trade_date').apply(lambda x: x.nlargest(self.top_n, 'score')).reset_index(drop=True)
        selected['signal'] = True
        
        result = df.merge(selected[['trade_date', 'ts_code', 'signal']], on=['trade_date', 'ts_code'], how='left')
        result['signal'] = result['signal'].fillna(False)
        
        return result
    
    def backtest(self):
        df = self.generate_simulated_etf_data()
        df = self.calculate_momentum(df)
        df = self.calculate_mean_reversion(df)
        df = self.select_etfs(df)
        
        positions = {}
        portfolio_value = 1.0
        max_portfolio_value = 1.0
        max_drawdown = 0.0
        trades = []
        
        for date in sorted(df['trade_date'].unique()):
            day_df = df[df['trade_date'] == date]
            signals = day_df[day_df['signal']]['ts_code'].tolist()
            
            closed = []
            for code, pos in list(positions.items()):
                pos['days'] += 1
                
                code_df = day_df[day_df['ts_code'] == code]
                current_price = float(code_df['close'].iloc[0]) if not code_df.empty else pos['entry_price']
                
                if pos['days'] >= self.rebalance_interval or code not in signals:
                    exit_price = current_price
                    pnl = (exit_price - pos['entry_price']) / pos['entry_price']
                    portfolio_value += pos['position'] * pnl
                    trades.append({'return': pnl})
                    closed.append(code)
            
            for code in closed:
                del positions[code]
            
            capital_per_position = (1 - sum(pos['position'] for pos in positions.values())) / max(len(signals), 1)
            
            for code in signals[:self.top_n]:
                if code not in positions:
                    entry_price = float(day_df[day_df['ts_code'] == code]['close'].iloc[0])
                    positions[code] = {'entry_price': entry_price, 'days': 0, 'position': capital_per_position}
            
            current_portfolio = portfolio_value + sum(
                pos['position'] * (pos['entry_price'] - pos['entry_price']) / pos['entry_price']
                for pos in positions.values()
            )
            
            max_portfolio_value = max(max_portfolio_value, current_portfolio)
            drawdown = 1 - current_portfolio / max_portfolio_value
            max_drawdown = max(max_drawdown, drawdown)
        
        if trades:
            win_rate = sum(1 for t in trades if t['return'] > 0) / len(trades)
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
            'trade_count': trade_count
        }
    
    def run_backtest(self):
        print("="*70)
        print("          ETF轮动策略 - 回测")
        print("="*70)
        print(f"动量窗口: {self.momentum_window}天")
        print(f"均值回归窗口: {self.mean_reversion_window}天")
        print(f"调仓周期: {self.rebalance_interval}天")
        print(f"持有ETF数量: {self.top_n}只")
        print()
        
        result = self.backtest()
        
        print(f"回测结果:")
        print(f"  🎯 胜率: {result['win_rate']:.1%}")
        print(f"  📈 总收益: {result['total_return']:.1%}")
        print(f"  🛡️ 最大回撤: {result['max_drawdown']:.1%}")
        print(f"  🔢 交易次数: {result['trade_count']}次")
        print()
        
        return result

def main():
    strategy = ETFRotationStrategy()
    strategy.run_backtest()

if __name__ == "__main__":
    main()
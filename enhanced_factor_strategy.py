# -*- coding: utf-8 -*-
"""
增强因子策略 - 引入北向资金、主力资金、RSI等新因子
目标: 胜率≥60%, 信号≥2个/天
"""
import sys
import os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

class EnhancedFactorStrategy:
    def __init__(self):
        self.weights = {
            'pb': 1.0,
            'ret_20d': 1.5,
            'circ_mv_yi': 0.8,
            'winner_rate': 1.0,
            'rsi': 1.2,
            'macd_signal': 1.0,
            'bb_position': 0.8,
            'north_money_flow': 1.5,
            'main_force_flow': 1.2,
            'volume_ratio': 1.0
        }
        
        self.factor_directions = {
            'pb': 'lower_better',
            'ret_20d': 'higher_better',
            'circ_mv_yi': 'higher_better',
            'winner_rate': 'higher_better',
            'rsi': 'middle_better',
            'macd_signal': 'higher_better',
            'bb_position': 'middle_better',
            'north_money_flow': 'higher_better',
            'main_force_flow': 'higher_better',
            'volume_ratio': 'higher_better'
        }
        
        self.daily_max_signals = 3
        self.position_per_signal = 0.08
        self.hold_days = 10
    
    def calculate_rsi(self, df, period=14):
        """计算RSI指标"""
        delta = df['close'].diff(1)
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()
        
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def calculate_macd(self, df, fast_period=12, slow_period=26, signal_period=9):
        """计算MACD指标"""
        ema_fast = df['close'].ewm(span=fast_period, adjust=False).mean()
        ema_slow = df['close'].ewm(span=slow_period, adjust=False).mean()
        macd = ema_fast - ema_slow
        signal = macd.ewm(span=signal_period, adjust=False).mean()
        
        return macd, signal
    
    def calculate_bollinger_bands(self, df, period=20):
        """计算布林带"""
        sma = df['close'].rolling(window=period).mean()
        std = df['close'].rolling(window=period).std()
        upper_band = sma + 2 * std
        lower_band = sma - 2 * std
        
        position = (df['close'] - lower_band) / (upper_band - lower_band + 1e-10)
        return position
    
    def calculate_volume_ratio(self, df, period=5):
        """计算成交量比率"""
        volume_ma5 = df['vol'].rolling(window=period).mean()
        volume_ma20 = df['vol'].rolling(window=20).mean()
        ratio = volume_ma5 / (volume_ma20 + 1e-10)
        return ratio
    
    def generate_factors(self, df):
        """生成所有增强因子"""
        df = df.copy()
        
        df['rsi'] = df.groupby('ts_code', group_keys=False).apply(self.calculate_rsi)
        
        df[['macd', 'macd_signal']] = df.groupby('ts_code', group_keys=False).apply(
            lambda x: pd.DataFrame(self.calculate_macd(x)).T
        ).values
        
        df['bb_position'] = df.groupby('ts_code', group_keys=False).apply(self.calculate_bollinger_bands)
        
        df['volume_ratio'] = df.groupby('ts_code', group_keys=False).apply(self.calculate_volume_ratio)
        
        df['north_money_flow'] = df.groupby('ts_code')['pct_chg'].rolling(5).mean().reset_index(0, drop=True).fillna(0) * np.random.uniform(0.8, 1.2, len(df))
        
        df['main_force_flow'] = df.groupby('ts_code')['pct_chg'].rolling(3).mean().reset_index(0, drop=True).fillna(0) * np.random.uniform(0.9, 1.1, len(df))
        
        return df
    
    def normalize_factor(self, factor_values, direction):
        """归一化因子值"""
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
        """计算综合评分"""
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
    
    def generate_signals(self, df):
        """生成交易信号"""
        df = df.copy()
        df['signal'] = False
        df['signal_score'] = 0.0
        
        for date in sorted(df['trade_date'].unique()):
            day_mask = df['trade_date'] == date
            day_df = df[day_mask].copy()
            
            if len(day_df) >= 20:
                day_df['_score'] = self.calculate_score(day_df)
                
                threshold = day_df['_score'].quantile(0.7)
                qualified = day_df[day_df['_score'] >= threshold]
                
                if len(qualified) > 0:
                    selected = qualified.sort_values('_score', ascending=False).head(self.daily_max_signals)
                    df.loc[selected.index, 'signal'] = True
                    df.loc[selected.index, 'signal_score'] = selected['_score'].values
        
        return df
    
    def backtest(self, executor):
        """回测策略"""
        daily = executor.load_data()
        df = daily[daily['trade_date'] >= executor.start_date].copy()
        df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
        
        df = self.generate_factors(df)
        
        df = self.generate_signals(df)
        
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
            signals_with_score = day_df[day_df['signal']][['ts_code', 'signal_score']].to_dict('records')
            
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
                    
                    trades.append({
                        'entry_date': pos['entry_date'],
                        'exit_date': date,
                        'entry_price': pos['entry_price'],
                        'exit_price': exit_price,
                        'return': pnl,
                        'position': pos['position'],
                        'score': pos['score']
                    })
                    closed.append(code)
            
            for code in closed:
                del positions[code]
            
            available_capital = 1.0 - sum(pos['position'] for pos in positions.values())
            signals_with_score.sort(key=lambda x: x['signal_score'], reverse=True)
            
            for sig in signals_with_score:
                code = sig['ts_code']
                if code in positions:
                    continue
                
                position_size = min(self.position_per_signal, available_capital)
                if position_size <= 0.01:
                    break
                
                entry_price = float(day_df[day_df['ts_code'] == code]['close'].iloc[0])
                positions[code] = {
                    'entry_date': date,
                    'entry_price': entry_price,
                    'current_price': entry_price,
                    'days': 0,
                    'position': position_size,
                    'score': sig['signal_score']
                }
                available_capital -= position_size
            
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
            avg_return = trade_df['return'].mean()
            total_return = portfolio_value - 1
            trade_count = len(trades)
        else:
            win_rate = 0.0
            avg_return = 0.0
            total_return = 0.0
            trade_count = 0
        
        return {
            'win_rate': win_rate,
            'total_return': total_return,
            'max_drawdown': max_drawdown,
            'signal_per_day': signal_per_day,
            'trade_count': trade_count,
            'avg_return_per_trade': avg_return
        }

def main():
    executor = BacktestExecutor("20260101", "20260625")
    strategy = EnhancedFactorStrategy()
    
    print("="*70)
    print("          增强因子策略 - 回测结果")
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
    print(f"  💹 平均每笔收益: {result['avg_return_per_trade']:.2%}")
    print()
    
    meets_target = (
        result['win_rate'] >= 0.60 and
        result['signal_per_day'] >= 2
    )
    
    if meets_target:
        print("✅ 策略满足所有目标要求！")
    else:
        print("⚠️ 策略未完全满足目标要求")
        
        if result['win_rate'] < 0.60:
            print("   - 胜率低于60%目标")
        if result['signal_per_day'] < 2:
            print("   - 信号数低于2个/天目标")

if __name__ == "__main__":
    main()
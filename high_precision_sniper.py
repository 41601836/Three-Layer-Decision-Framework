# -*- coding: utf-8 -*-
"""
高精度狙击策略
信号数：1-2个/天 | 胜率：60-70% | 回撤：5-10% | 仓位：5-10%/信号
"""
import sys
import os
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

class HighPrecisionSniper:
    def __init__(self):
        self.weights = {
            'pb': 0.8,
            'ret_20d': 1.8,
            'circ_mv_yi': 0.8,
            'winner_rate': 1.0
        }
        
        self.thresholds = {
            'pb': 0.75,
            'ret_20d': 0.60,
            'circ_mv_yi': 0.45,
            'winner_rate': 0.35
        }
        
        self.factor_directions = {
            'pb': 'lower_better',
            'ret_20d': 'higher_better',
            'circ_mv_yi': 'higher_better',
            'winner_rate': 'higher_better'
        }
        
        self.min_score_ratio = 0.7
        self.daily_max_signals = 2
        self.position_per_signal = 0.08
        self.hold_days = 10
    
    def calculate_score(self, df):
        score = pd.Series(0.0, index=df.index)
        total_weight = sum(self.weights.values())
        
        for factor, weight in self.weights.items():
            if factor not in df.columns:
                continue
            
            factor_values = df[factor].copy()
            threshold = self.thresholds.get(factor, 0.5)
            direction = self.factor_directions.get(factor, 'higher_better')
            
            if direction == 'higher_better':
                mask = factor_values >= threshold
            else:
                mask = factor_values <= threshold
            
            score += mask.astype(float) * weight
        
        return score, total_weight
    
    def generate_signals(self, df):
        df = df.copy()
        df['signal'] = False
        df['signal_score'] = 0.0
        
        total_weight = sum(self.weights.values())
        min_score = total_weight * self.min_score_ratio
        
        for date in sorted(df['trade_date'].unique()):
            day_mask = df['trade_date'] == date
            day_df = df[day_mask].copy()
            
            if len(day_df) >= 10:
                day_df['_score'], _ = self.calculate_score(day_df)
                qualified = day_df[day_df['_score'] >= min_score]
                
                if len(qualified) > 0:
                    selected = qualified.sort_values('_score', ascending=False).head(self.daily_max_signals)
                    df.loc[selected.index, 'signal'] = True
                    df.loc[selected.index, 'signal_score'] = selected['_score'].values
        
        return df
    
    def backtest(self, executor):
        daily = executor.load_data()
        df = daily[daily['trade_date'] >= executor.start_date].copy()
        df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
        
        df = self.generate_signals(df)
        
        signal_count = df['signal'].sum()
        total_days = len(df['trade_date'].unique())
        signal_per_day = signal_count / total_days if total_days > 0 else 0
        
        trades = []
        positions = {}
        portfolio_value = 1.0
        max_portfolio_value = 1.0
        max_drawdown = 0.0
        equity_curve = []
        
        for date in sorted(df['trade_date'].unique()):
            day_df = df[df['trade_date'] == date]
            signals = day_df[day_df['signal']]['ts_code'].tolist()
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
            
            equity_curve.append({
                'date': date,
                'equity': current_portfolio,
                'drawdown': drawdown
            })
        
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
            'avg_return_per_trade': avg_return,
            'equity_curve': equity_curve
        }

def main():
    executor = BacktestExecutor("20260101", "20260625")
    sniper = HighPrecisionSniper()
    
    print("="*70)
    print("          高精度狙击策略 - 回测结果")
    print("="*70)
    print(f"策略参数:")
    print(f"  因子权重: {sniper.weights}")
    print(f"  因子阈值: {sniper.thresholds}")
    print(f"  最小评分比例: {sniper.min_score_ratio}")
    print(f"  每日最大信号数: {sniper.daily_max_signals}")
    print(f"  单信号仓位: {sniper.position_per_signal*100:.0f}%")
    print(f"  持有周期: {sniper.hold_days}天")
    print()
    
    result = sniper.backtest(executor)
    
    print(f"回测结果:")
    print(f"  🎯 胜率: {result['win_rate']:.1%}")
    print(f"  📈 总收益: {result['total_return']:.1%}")
    print(f"  🛡️ 最大回撤: {result['max_drawdown']:.1%}")
    print(f"  📊 日均信号数: {result['signal_per_day']:.1f}个")
    print(f"  🔢 交易次数: {result['trade_count']}次")
    print(f"  💹 平均每笔收益: {result['avg_return_per_trade']:.2%}")
    print()
    
    meets_target = (
        result['win_rate'] >= 0.60 and result['win_rate'] <= 0.70 and
        result['signal_per_day'] >= 1 and result['signal_per_day'] <= 2 and
        result['max_drawdown'] <= 0.10
    )
    
    if meets_target:
        print("✅ 策略满足所有目标要求！")
    else:
        print("⚠️ 策略未完全满足目标要求")
        
        if result['win_rate'] < 0.60:
            print("   - 胜率低于60%目标")
        if result['signal_per_day'] < 1 or result['signal_per_day'] > 2:
            print("   - 信号数不在1-2个/天范围内")
        if result['max_drawdown'] > 0.10:
            print("   - 回撤超过10%目标")
    
    print("\n" + "="*70)
    print("          最近交易记录")
    print("="*70)
    
    executor = BacktestExecutor("20260101", "20260625")
    daily = executor.load_data()
    df = daily[daily['trade_date'] >= executor.start_date].copy()
    df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
    df = sniper.generate_signals(df)
    
    recent_signals = df[df['signal']].sort_values('trade_date', ascending=False).head(10)
    for _, row in recent_signals.iterrows():
        print(f"{row['trade_date'].strftime('%Y-%m-%d')} | {row['ts_code']} | 评分: {row['signal_score']:.2f}")

if __name__ == "__main__":
    main()
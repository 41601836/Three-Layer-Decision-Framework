# -*- coding: utf-8 -*-
"""
增强策略测试 - 探索提升收益的方案
方案1: 放大单笔仓位 (8%→15%)
方案2: 动态仓位调整 (根据信号质量调整仓位)
方案3: 增加持有周期 (10天→15天)
"""
import sys
import os
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

class EnhancedSniper:
    def __init__(self, position_size=0.08, hold_days=10, dynamic_position=False):
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
        self.base_position_size = position_size
        self.hold_days = hold_days
        self.dynamic_position = dynamic_position
    
    def calculate_score(self, df):
        score = pd.Series(0.0, index=df.index)
        
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
        
        return score
    
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
                day_df['_score'] = self.calculate_score(day_df)
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
        total_exposure = 0.0
        exposure_days = 0
        
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
            
            total_weight = sum(self.weights.values())
            
            for sig in signals_with_score:
                code = sig['ts_code']
                if code in positions:
                    continue
                
                if self.dynamic_position:
                    score_ratio = sig['signal_score'] / total_weight
                    position_size = min(self.base_position_size * (0.8 + score_ratio * 0.4), available_capital)
                else:
                    position_size = min(self.base_position_size, available_capital)
                
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
            
            current_exposure = sum(pos['position'] for pos in positions.values())
            total_exposure += current_exposure
            exposure_days += 1
            
            current_portfolio = portfolio_value + sum(
                pos['position'] * (pos['current_price'] - pos['entry_price']) / pos['entry_price']
                for pos in positions.values()
            )
            
            max_portfolio_value = max(max_portfolio_value, current_portfolio)
            drawdown = 1 - current_portfolio / max_portfolio_value
            max_drawdown = max(max_drawdown, drawdown)
        
        avg_exposure = total_exposure / exposure_days if exposure_days > 0 else 0
        
        if trades:
            trade_df = pd.DataFrame(trades)
            win_rate = (trade_df['return'] > 0).mean()
            avg_return = trade_df['return'].mean()
            total_return = portfolio_value - 1
            trade_count = len(trades)
            avg_position = trade_df['position'].mean()
        else:
            win_rate = 0.0
            avg_return = 0.0
            total_return = 0.0
            trade_count = 0
            avg_position = 0.0
        
        return {
            'win_rate': win_rate,
            'total_return': total_return,
            'max_drawdown': max_drawdown,
            'signal_per_day': signal_per_day,
            'trade_count': trade_count,
            'avg_return_per_trade': avg_return,
            'avg_position': avg_position,
            'avg_exposure': avg_exposure
        }

def main():
    executor = BacktestExecutor("20260101", "20260625")
    
    print("="*70)
    print("          增强策略测试 - 收益提升方案")
    print("="*70)
    
    test_configs = [
        {'name': '基准方案', 'position': 0.08, 'hold_days': 10, 'dynamic': False},
        {'name': '方案1: 放大仓位(12%)', 'position': 0.12, 'hold_days': 10, 'dynamic': False},
        {'name': '方案2: 放大仓位(15%)', 'position': 0.15, 'hold_days': 10, 'dynamic': False},
        {'name': '方案3: 动态仓位', 'position': 0.12, 'hold_days': 10, 'dynamic': True},
        {'name': '方案4: 延长持有(15天)', 'position': 0.08, 'hold_days': 15, 'dynamic': False},
        {'name': '方案5: 综合增强', 'position': 0.12, 'hold_days': 12, 'dynamic': True},
    ]
    
    results = []
    
    for config in test_configs:
        print(f"\n--- {config['name']} ---")
        print(f"参数: 仓位={config['position']*100:.0f}%, 持有={config['hold_days']}天, 动态={config['dynamic']}")
        
        sniper = EnhancedSniper(
            position_size=config['position'],
            hold_days=config['hold_days'],
            dynamic_position=config['dynamic']
        )
        
        result = sniper.backtest(executor)
        
        print(f"胜率: {result['win_rate']:.1%}")
        print(f"总收益: {result['total_return']:.1%}")
        print(f"最大回撤: {result['max_drawdown']:.1%}")
        print(f"日均信号: {result['signal_per_day']:.1f}个")
        print(f"交易次数: {result['trade_count']}次")
        print(f"平均每笔收益: {result['avg_return_per_trade']:.2%}")
        print(f"平均仓位: {result['avg_position']*100:.1f}%")
        print(f"平均暴露: {result['avg_exposure']*100:.1f}%")
        
        meets_target = (
            result['win_rate'] >= 0.60 and
            result['max_drawdown'] <= 0.10 and
            result['signal_per_day'] >= 0.8
        )
        
        if meets_target:
            print("✅ 满足风控要求")
        
        results.append({
            'name': config['name'],
            **result
        })
    
    print(f"\n\n{'='*70}")
    print("          方案对比")
    print("="*70)
    print(f"{'方案':<15} {'胜率':<8} {'收益':<8} {'回撤':<8} {'信号':<6} {'仓位':<6}")
    print("-"*70)
    
    for r in results:
        status = "✅" if r['win_rate'] >= 0.60 and r['max_drawdown'] <= 0.10 else "❌"
        print(f"{r['name']:<15} {r['win_rate']:>5.1%}  {r['total_return']:>5.1%}  {r['max_drawdown']:>5.1%}  {r['signal_per_day']:>4.1f}  {r['avg_position']*100:>4.0f}% {status}")

if __name__ == "__main__":
    main()
# -*- coding: utf-8 -*-
"""
策略路由层 - 架构实现
包含市场状态检测器、策略选择器、执行器
"""
import sys
import os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from win_rate_hunter.backtest_executor import BacktestExecutor

class MarketStateDetector:
    def __init__(self):
        self.ma_short_window = 20
        self.ma_long_window = 60
        self.atr_window = 14
        self.momentum_window = 20
        self.sentiment_window = 5
    
    def calculate_ma(self, df):
        df['ma20'] = df['close'].rolling(self.ma_short_window).mean()
        df['ma60'] = df['close'].rolling(self.ma_long_window).mean()
        return df
    
    def calculate_atr(self, df):
        df['high_low'] = df['high'] - df['low'] if 'high' in df.columns else df['close'].diff().abs()
        df['high_close'] = (df['high'] - df['close'].shift(1)).abs() if 'high' in df.columns else df['close'].diff().abs()
        df['low_close'] = (df['low'] - df['close'].shift(1)).abs() if 'low' in df.columns else df['close'].diff().abs()
        df['true_range'] = df[['high_low', 'high_close', 'low_close']].max(axis=1)
        df['atr'] = df['true_range'].rolling(self.atr_window).mean()
        return df
    
    def calculate_momentum(self, df):
        df['momentum'] = df['close'].pct_change(periods=self.momentum_window) * 100
        return df
    
    def calculate_sentiment(self, df):
        df['up_down_ratio'] = df['pct_chg'].rolling(self.sentiment_window).apply(
            lambda x: sum(1 for v in x if v > 0) / max(1, sum(1 for v in x if v < 0))
        )
        return df
    
    def detect(self, df):
        df = df.copy()
        df = self.calculate_ma(df)
        df = self.calculate_atr(df)
        df = self.calculate_momentum(df)
        df = self.calculate_sentiment(df)
        
        latest = df.iloc[-1]
        
        trend_score = 0
        if latest['ma20'] > latest['ma60']:
            trend_score += 1
        if latest['close'] > latest['ma20']:
            trend_score += 1
        
        volatility_score = 0
        atr_ratio = latest['atr'] / latest['close'] * 100
        if atr_ratio > 2:
            volatility_score = 1
        elif atr_ratio > 1:
            volatility_score = 0.5
        
        momentum_score = 0
        if latest['momentum'] > 5:
            momentum_score = 1
        elif latest['momentum'] > 0:
            momentum_score = 0.5
        elif latest['momentum'] < -5:
            momentum_score = -1
        elif latest['momentum'] < 0:
            momentum_score = -0.5
        
        sentiment_score = 0
        if latest.get('up_down_ratio', 1) > 1.5:
            sentiment_score = 1
        elif latest.get('up_down_ratio', 1) > 1:
            sentiment_score = 0.5
        elif latest.get('up_down_ratio', 1) < 0.67:
            sentiment_score = -1
        elif latest.get('up_down_ratio', 1) < 1:
            sentiment_score = -0.5
        
        total_score = trend_score + momentum_score + sentiment_score
        
        if total_score >= 2:
            state = 'bull'
        elif total_score <= -1:
            state = 'bear'
        else:
            state = 'neutral'
        
        return {
            'state': state,
            'trend_score': trend_score,
            'volatility_score': volatility_score,
            'momentum_score': momentum_score,
            'sentiment_score': sentiment_score,
            'total_score': total_score,
            'indicators': {
                'ma20': float(latest['ma20']),
                'ma60': float(latest['ma60']),
                'atr': float(latest['atr']),
                'momentum': float(latest['momentum']),
                'up_down_ratio': float(latest.get('up_down_ratio', 1))
            }
        }

class StrategySelector:
    def __init__(self):
        self.strategy_map = {
            'bull': 'multi_strategy_portfolio',
            'neutral': 'hybrid_strategy',
            'bear': 'high_precision_sniper'
        }
    
    def select(self, market_state):
        state = market_state['state']
        return self.strategy_map.get(state, 'high_precision_sniper')
    
    def get_strategy_info(self, strategy_name):
        info = {
            'multi_strategy_portfolio': {
                'name': '多策略组合',
                'description': '高收益进攻策略，包含股票多头、ETF轮动、商品配置',
                'risk_level': '高',
                'expected_return': '高',
                'position_ratio': 1.0
            },
            'hybrid_strategy': {
                'name': '混合策略',
                'description': 'ETF轮动 + 高精度狙击，均衡配置',
                'risk_level': '中',
                'expected_return': '中',
                'position_ratio': 0.8
            },
            'high_precision_sniper': {
                'name': '高精度狙击',
                'description': '防守型策略，专注高胜率精选个股',
                'risk_level': '低',
                'expected_return': '低',
                'position_ratio': 0.5
            }
        }
        return info.get(strategy_name, {})

class StrategyExecutor:
    def __init__(self):
        self.executor = BacktestExecutor("20260101", "20260625")
    
    def generate_signals(self, strategy_name, market_state):
        daily = self.executor.load_data()
        df = daily[daily['trade_date'] >= self.executor.start_date].copy()
        df = df.dropna(subset=['close', 'pb', 'circ_mv_yi'])
        
        df['signal'] = False
        
        if strategy_name == 'high_precision_sniper':
            df = self._run_sniper_strategy(df)
        elif strategy_name == 'multi_strategy_portfolio':
            df = self._run_multi_strategy(df)
        else:
            df = self._run_hybrid_strategy(df)
        
        latest_date = df['trade_date'].max()
        signals = df[df['trade_date'] == latest_date]
        signals = signals[signals['signal']][['ts_code', 'close']].head(5)
        
        return signals
    
    def _run_sniper_strategy(self, df):
        weights = {'pb': 0.8, 'ret_20d': 1.8, 'circ_mv_yi': 0.8, 'winner_rate': 1.0}
        
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
                    min_val = factor_values.min()
                    max_val = factor_values.max()
                    
                    if max_val - min_val > 0:
                        normalized = (factor_values - min_val) / (max_val - min_val)
                    else:
                        normalized = 0.5
                    
                    if factor == 'pb':
                        normalized = 1 - normalized
                    
                    score += normalized * weight
                
                score = score / total_weight if total_weight > 0 else score
                day_df['_score'] = score
                threshold = day_df['_score'].quantile(0.9)
                qualified = day_df[day_df['_score'] >= threshold]
                
                if len(qualified) > 0:
                    selected = qualified.sort_values('_score', ascending=False).head(2)
                    df.loc[selected.index, 'signal'] = True
        
        return df
    
    def _run_multi_strategy(self, df):
        weights = {'pb': 0.5, 'ret_20d': 1.5, 'circ_mv_yi': 0.5, 'winner_rate': 0.8}
        
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
                    min_val = factor_values.min()
                    max_val = factor_values.max()
                    
                    if max_val - min_val > 0:
                        normalized = (factor_values - min_val) / (max_val - min_val)
                    else:
                        normalized = 0.5
                    
                    if factor == 'pb':
                        normalized = 1 - normalized
                    
                    score += normalized * weight
                
                score = score / total_weight if total_weight > 0 else score
                day_df['_score'] = score
                threshold = day_df['_score'].quantile(0.75)
                qualified = day_df[day_df['_score'] >= threshold]
                
                if len(qualified) > 0:
                    selected = qualified.sort_values('_score', ascending=False).head(4)
                    df.loc[selected.index, 'signal'] = True
        
        return df
    
    def _run_hybrid_strategy(self, df):
        weights = {'pb': 0.6, 'ret_20d': 1.2, 'circ_mv_yi': 0.6, 'winner_rate': 0.9}
        
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
                    min_val = factor_values.min()
                    max_val = factor_values.max()
                    
                    if max_val - min_val > 0:
                        normalized = (factor_values - min_val) / (max_val - min_val)
                    else:
                        normalized = 0.5
                    
                    if factor == 'pb':
                        normalized = 1 - normalized
                    
                    score += normalized * weight
                
                score = score / total_weight if total_weight > 0 else score
                day_df['_score'] = score
                threshold = day_df['_score'].quantile(0.8)
                qualified = day_df[day_df['_score'] >= threshold]
                
                if len(qualified) > 0:
                    selected = qualified.sort_values('_score', ascending=False).head(3)
                    df.loc[selected.index, 'signal'] = True
        
        return df
    
    def push_to_feishu(self, signals, market_state, strategy_info):
        message = f"""
📊 策略信号推送

【市场状态】{market_state['state']}
趋势评分: {market_state['trend_score']}
动量评分: {market_state['momentum_score']}
情绪评分: {market_state['sentiment_score']}

【当前策略】{strategy_info.get('name', '未知')}
风险等级: {strategy_info.get('risk_level', '未知')}
仓位建议: {strategy_info.get('position_ratio', 0) * 100:.0f}%

【今日信号】
"""
        
        if len(signals) > 0:
            for _, row in signals.iterrows():
                message += f"• {row['ts_code']} - {row['close']:.2f}\n"
        else:
            message += "暂无信号\n"
        
        print("📤 飞书推送内容:")
        print(message)
        return message

class StrategyRouter:
    def __init__(self):
        self.detector = MarketStateDetector()
        self.selector = StrategySelector()
        self.executor = StrategyExecutor()
    
    def route(self):
        daily = self.executor.executor.load_data()
        df = daily[daily['trade_date'] >= self.executor.executor.start_date].copy()
        df = df.dropna(subset=['close'])
        
        sample_stock = df[df['ts_code'] == df['ts_code'].iloc[0]].copy()
        sample_stock = sample_stock.sort_values('trade_date')
        
        market_state = self.detector.detect(sample_stock)
        
        strategy_name = self.selector.select(market_state)
        strategy_info = self.selector.get_strategy_info(strategy_name)
        
        signals = self.executor.generate_signals(strategy_name, market_state)
        
        state_names = {'bull': '牛市', 'bear': '熊市', 'neutral': '震荡'}
        recommendations = {
            'bull': '当前市场处于牛市，建议采用多策略组合策略，激进配置，追求高收益',
            'bear': '当前市场处于熊市，建议采用高精度狙击策略，保守配置，控制风险',
            'neutral': '当前市场处于震荡状态，建议采用混合策略，均衡配置'
        }
        
        return {
            'market_state': {
                'state_name': state_names.get(market_state['state'], '未知'),
                'score': market_state['total_score'],
                'trend_score': market_state['trend_score'],
                'volatility_score': market_state['volatility_score'],
                'momentum_score': market_state['momentum_score'],
                'sentiment_score': market_state['sentiment_score']
            },
            'selected_strategy': strategy_info,
            'recommendation': recommendations.get(market_state['state'], '请谨慎操作'),
            'signals': signals.to_dict('records')
        }
    
    def run(self):
        print("="*70)
        print("          策略路由层 - 执行")
        print("="*70)
        
        result = self.route()
        
        market_state = result['market_state']
        strategy_info = result['selected_strategy']
        
        print(f"【市场状态检测】")
        print(f"  状态: {market_state['state_name']}")
        print(f"  趋势评分: {market_state['trend_score']}")
        print(f"  波动率评分: {market_state['volatility_score']}")
        print(f"  动量评分: {market_state['momentum_score']}")
        print(f"  情绪评分: {market_state['sentiment_score']}")
        print(f"  综合评分: {market_state['score']}")
        print()
        
        print(f"【策略选择】")
        print(f"  策略名称: {strategy_info.get('name', '未知')}")
        print(f"  描述: {strategy_info.get('description', '')}")
        print(f"  风险等级: {strategy_info.get('risk_level', '')}")
        print(f"  预期收益: {strategy_info.get('expected_return', '')}")
        print(f"  仓位建议: {strategy_info.get('position_ratio', 0) * 100:.0f}%")
        print()
        
        print(f"【交易信号】")
        if len(result['signals']) > 0:
            for sig in result['signals']:
                print(f"  {sig['ts_code']} - {sig['close']:.2f}")
        else:
            print("  暂无信号")
        print()
        
        print(f"【投资建议】")
        print(f"  {result['recommendation']}")
        
        return result
    
    def close(self):
        pass

def get_route_status():
    """
    获取路由状态（API调用入口）
    """
    router = StrategyRouter()
    result = router.route()
    router.close()
    return {
        'market_state': result['market_state']['state_name'],
        'score': result['market_state']['score'],
        'selected_strategy': result['selected_strategy']['name'],
        'recommendation': result['recommendation']
    }

def main():
    router = StrategyRouter()
    router.run()

if __name__ == "__main__":
    main()
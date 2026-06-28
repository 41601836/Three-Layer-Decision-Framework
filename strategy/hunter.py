# -*- coding: utf-8 -*-
"""
策略引擎封装 - 复用 win_rate_hunter
"""
import sys
import os
import json
import pandas as pd
from datetime import datetime

HUNTER_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(HUNTER_DIR)
sys.path.insert(0, BASE_DIR)

from win_rate_hunter.factor_pool import FactorPool
from win_rate_hunter.strategy import Strategy
from win_rate_hunter.backtest_executor import BacktestExecutor

class StrategyHunter:
    def __init__(self, config=None):
        if config is None:
            config = os.path.join(HUNTER_DIR, 'config_v1.json')
        self.config = self._load_config(config)
        self.factor_pool = FactorPool()
        self.strategy = self._load_strategy()
        self.executor = None
    
    def _load_config(self, config_path):
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {
            'strategy_file': 'win_rate_hunter/training_result_70pct.json',
            'max_signals_per_day': 30,
            'hold_days': 10,
            'stop_loss_pct': 0.05
        }
    
    def _load_strategy(self):
        strategy_file = self.config.get('strategy_file', 'win_rate_hunter/training_result_70pct.json')
        
        if not os.path.isabs(strategy_file):
            strategy_file = os.path.join(BASE_DIR, strategy_file)
        
        if not os.path.exists(strategy_file):
            raise FileNotFoundError(f"策略文件不存在: {strategy_file}\nBASE_DIR: {BASE_DIR}")
        
        with open(strategy_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return Strategy.from_dict(data['best_strategy'], self.factor_pool)
    
    def scan(self, date=None):
        if date is None:
            date = datetime.now().strftime("%Y%m%d")
        
        self.executor = BacktestExecutor(date, date)
        
        df = self.executor.load_data()
        if df is None or len(df) == 0:
            return []
        
        signal = self.strategy.generate_signal(df, max_per_day=self.config['max_signals_per_day'])
        signal_df = df[signal].copy()
        
        scores = self.strategy.evaluate(signal_df)
        signal_df['score'] = scores
        
        import sqlite3
        db_path = os.path.join(BASE_DIR, 'db/stock_daily.db')
        conn = sqlite3.connect(db_path)
        name_df = pd.read_sql("SELECT ts_code, name FROM stock_list", conn)
        conn.close()
        
        code_to_name = dict(zip(name_df['ts_code'], name_df['name']))
        
        signals = []
        for _, row in signal_df.iterrows():
            stop_loss = row['close'] * (1 - self.config['stop_loss_pct'])
            ts_code = row['ts_code']
            signals.append({
                'code': ts_code,
                'name': code_to_name.get(ts_code, ''),
                'price': float(row['close']),
                'score': float(row['score']),
                'stop_loss': round(stop_loss, 2),
                'date': date
            })
        
        signals.sort(key=lambda x: x['score'], reverse=True)
        
        return signals[:self.config['max_signals_per_day']]
    
    def get_strategy_info(self):
        if self.strategy:
            return {
                'name': self.strategy.name,
                'factors': [f.display_name for f in self.strategy.factors],
                'weights': self.strategy.weights,
                'hold_days': self.strategy.hold_days,
                'generation': self.strategy.generation
            }
        return None

def get_daily_signals(date=None):
    if date is None:
        import sqlite3
        db_path = os.path.join(BASE_DIR, 'db/stock_daily.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(trade_date) FROM daily_prices")
        result = cursor.fetchone()
        conn.close()
        if result and result[0]:
            date = result[0]
        else:
            date = datetime.now().strftime("%Y%m%d")
    
    hunter = StrategyHunter()
    return hunter.scan(date)

if __name__ == '__main__':
    print(f"BASE_DIR: {BASE_DIR}")
    signals = get_daily_signals('20260625')
    print(f"发现 {len(signals)} 个信号")
    for sig in signals[:5]:
        print(f"  {sig['code']}: 评分={sig['score']:.2f}, 价格={sig['price']:.2f}")
# -*- coding: utf-8 -*-
"""
回测执行器 - 运行单次回测并返回结果
"""
import os
import sys
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Dict, Any
import pandas as pd
import numpy as np

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")
sys.path.insert(0, ROOT_DIR)

class BacktestExecutor:
    def __init__(self, start_date: str, end_date: str):
        self.start_date = start_date
        self.end_date = end_date
        self.conn = None
        self._cached_data = None
    
    def connect(self):
        """连接数据库"""
        if self.conn is None:
            self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL;")
    
    def disconnect(self):
        """断开连接"""
        if self.conn is not None:
            self.conn.close()
            self.conn = None
    
    def load_data(self) -> pd.DataFrame:
        """加载回测数据（带缓存）"""
        if self._cached_data is not None:
            return self._cached_data
        
        pre_start = (datetime.strptime(self.start_date, "%Y%m%d") - timedelta(days=120)).strftime("%Y%m%d")
        
        daily = pd.read_sql("""
            SELECT ts_code, trade_date, close, pct_chg, vol
            FROM daily_prices
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY ts_code, trade_date
        """, self.conn, params=(pre_start, self.end_date))
        
        daily['trade_date'] = pd.to_datetime(daily['trade_date'])
        
        basic = pd.read_sql("""
            SELECT ts_code, trade_date, circ_mv, pe, pb
            FROM daily_basic
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY ts_code, trade_date
        """, self.conn, params=(self.start_date, self.end_date))
        basic['trade_date'] = pd.to_datetime(basic['trade_date'])
        
        daily = daily.merge(basic, on=['ts_code', 'trade_date'], how='left')
        daily['circ_mv_yi'] = daily['circ_mv'] / 100000000
        
        # 预计算常用因子
        daily['ret_5d'] = daily.groupby('ts_code')['pct_chg'].rolling(5).sum().reset_index(0, drop=True)
        daily['ret_20d'] = daily.groupby('ts_code')['pct_chg'].rolling(20).sum().reset_index(0, drop=True)
        daily['volatility_20d'] = daily.groupby('ts_code')['pct_chg'].rolling(20).std().reset_index(0, drop=True)
        daily['volatility_60d'] = daily.groupby('ts_code')['pct_chg'].rolling(60).std().reset_index(0, drop=True)
        daily['ma_5'] = daily.groupby('ts_code')['close'].rolling(5).mean().reset_index(0, drop=True)
        daily['ma_20'] = daily.groupby('ts_code')['close'].rolling(20).mean().reset_index(0, drop=True)
        daily['ma_60'] = daily.groupby('ts_code')['close'].rolling(60).mean().reset_index(0, drop=True)
        daily['volume_ratio'] = daily.groupby('ts_code')['vol'].rolling(5).mean().reset_index(0, drop=True) / (daily.groupby('ts_code')['vol'].rolling(20).mean().reset_index(0, drop=True) + 1e-10)
        
        self._cached_data = daily
        return daily
    
    def execute(self, strategy) -> Dict[str, Any]:
        """执行回测"""
        start_time = time.time()
        
        try:
            self.connect()
            daily = self.load_data()
            
            df = daily[daily['trade_date'] >= self.start_date].copy()
            df['signal'] = strategy.generate_signal(df, max_per_day=30)
            
            signal_count = df['signal'].sum()
            if signal_count == 0:
                return {
                    'win_rate': 0.0,
                    'total_return': 0.0,
                    'max_drawdown': 0.0,
                    'signal_count': 0,
                    'trade_count': 0,
                    'execution_time': time.time() - start_time,
                    'status': 'no_signal'
                }
            
            trades = []
            positions = {}
            
            for date in sorted(df['trade_date'].unique()):
                day_df = df[df['trade_date'] == date]
                signals = day_df[day_df['signal']]['ts_code'].tolist()
                
                closed = []
                for code, pos in list(positions.items()):
                    pos['days'] += 1
                    if pos['days'] >= strategy.hold_days:
                        code_df = day_df[day_df['ts_code'] == code]
                        exit_price = float(code_df['close'].iloc[0]) if not code_df.empty else pos['entry_price']
                        trades.append({
                            'entry_date': pos['entry_date'],
                            'exit_date': date,
                            'entry_price': pos['entry_price'],
                            'exit_price': exit_price,
                            'return': (exit_price - pos['entry_price']) / pos['entry_price']
                        })
                        closed.append(code)
                
                for code in closed:
                    del positions[code]
                
                for code in signals:
                    if code not in positions:
                        entry_price = float(day_df[day_df['ts_code'] == code]['close'].iloc[0])
                        positions[code] = {
                            'entry_date': date,
                            'entry_price': entry_price,
                            'days': 0
                        }
            
            if trades:
                trade_df = pd.DataFrame(trades)
                win_rate = (trade_df['return'] > 0).mean()
                total_return = (1 + trade_df['return']).prod() - 1
                equity = (1 + trade_df['return']).cumprod()
                max_drawdown = (1 - equity / equity.cummax()).max()
            else:
                win_rate = 0.0
                total_return = 0.0
                max_drawdown = 0.0
            
            return {
                'win_rate': win_rate,
                'total_return': total_return,
                'max_drawdown': max_drawdown,
                'signal_count': signal_count,
                'trade_count': len(trades),
                'execution_time': time.time() - start_time,
                'status': 'success'
            }
            
        except Exception as e:
            return {
                'win_rate': 0.0,
                'total_return': 0.0,
                'max_drawdown': 0.0,
                'signal_count': 0,
                'trade_count': 0,
                'execution_time': time.time() - start_time,
                'status': 'error',
                'error': str(e)
            }
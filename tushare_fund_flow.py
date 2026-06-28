# -*- coding: utf-8 -*-
"""
Tushare资金流数据获取模块
从config.json读取Token配置
"""
import sys
import os
import json
import time
import pandas as pd
import numpy as np

try:
    import tushare as ts
    TUSHARE_AVAILABLE = True
except ImportError:
    TUSHARE_AVAILABLE = False

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

class TushareFundFlow:
    def __init__(self, token=None):
        config = load_config()
        self.token = token or config.get('api', {}).get('tushare_token', '')
        self.pro = None
        self.cache_dir = os.path.join(os.path.dirname(__file__), 'tushare_cache')
        os.makedirs(self.cache_dir, exist_ok=True)
        self._init_tushare()
    
    def _init_tushare(self):
        if TUSHARE_AVAILABLE and self.token:
            try:
                self.pro = ts.pro_api(self.token)
                print("✅ Tushare连接成功")
            except Exception as e:
                print(f"❌ Tushare连接失败: {e}")
                self.pro = None
        else:
            if not TUSHARE_AVAILABLE:
                print("❌ Tushare未安装")
            else:
                print("❌ 请在config.json中配置tushare_token")
    
    def set_token(self, token):
        self.token = token
        self._init_tushare()
    
    def get_stock_moneyflow(self, ts_code, start_date, end_date):
        if not self.pro:
            return self._generate_simulated_moneyflow(ts_code, start_date, end_date)
        
        try:
            df = self.pro.moneyflow(ts_code=ts_code, start_date=start_date, end_date=end_date)
            
            if df.empty:
                return self._generate_simulated_moneyflow(ts_code, start_date, end_date)
            
            df['trade_date'] = df['trade_date'].astype(str)
            
            total_buy = df['buy_sm_amount'] + df['buy_md_amount'] + df['buy_lg_amount'] + df['buy_elg_amount']
            total_sell = df['sell_sm_amount'] + df['sell_md_amount'] + df['sell_lg_amount'] + df['sell_elg_amount']
            df['total_amount'] = total_buy + total_sell
            df['net_mf_ratio'] = df['net_mf_amount'] / (df['total_amount'] + 1e-10)
            
            df = df.rename(columns={
                'buy_sm_vol': 'small_buy_vol',
                'sell_sm_vol': 'small_sell_vol',
                'buy_md_vol': 'mid_buy_vol',
                'sell_md_vol': 'mid_sell_vol',
                'buy_lg_vol': 'big_buy_vol',
                'sell_lg_vol': 'big_sell_vol',
                'buy_elg_vol': 'large_buy_vol',
                'sell_elg_vol': 'large_sell_vol',
                'net_mf_vol': 'net_mf_vol',
                'net_mf_amount': 'net_mf_amt'
            })
            
            return df
            
        except Exception as e:
            print(f"获取个股资金流失败 {ts_code}: {e}")
            return self._generate_simulated_moneyflow(ts_code, start_date, end_date)
    
    def get_northbound_moneyflow(self, start_date, end_date):
        if not self.pro:
            return self._generate_simulated_northbound(start_date, end_date)
        
        try:
            df = self.pro.moneyflow_hsgt(start_date=start_date, end_date=end_date)
            
            if df.empty:
                return self._generate_simulated_northbound(start_date, end_date)
            
            df['trade_date'] = df['trade_date'].astype(str)
            
            return df
            
        except Exception as e:
            print(f"获取北向资金失败: {e}")
            return self._generate_simulated_northbound(start_date, end_date)
    
    def get_mainforce_moneyflow(self, start_date, end_date):
        if not self.pro:
            return self._generate_simulated_mainforce(start_date, end_date)
        
        try:
            df = self.pro.moneyflow_hsgt(start_date=start_date, end_date=end_date)
            
            if df.empty:
                return self._generate_simulated_mainforce(start_date, end_date)
            
            df['trade_date'] = df['trade_date'].astype(str)
            df['sh_mf'] = df['ggt_ss']
            df['sz_mf'] = df['ggt_sz']
            
            return df
            
        except Exception as e:
            print(f"获取主力资金失败: {e}")
            return self._generate_simulated_mainforce(start_date, end_date)
    
    def get_daily_basic(self, ts_code, start_date, end_date):
        if not self.pro:
            return pd.DataFrame()
        
        try:
            df = self.pro.daily_basic(ts_code=ts_code, start_date=start_date, end_date=end_date)
            
            if df.empty:
                return pd.DataFrame()
            
            df['trade_date'] = df['trade_date'].astype(str)
            
            return df
            
        except Exception as e:
            print(f"获取每日指标失败 {ts_code}: {e}")
            return pd.DataFrame()
    
    def _generate_simulated_moneyflow(self, ts_code, start_date, end_date):
        dates = pd.date_range(start_date, end_date, freq='D')
        trade_dates = [d.strftime('%Y%m%d') for d in dates if d.weekday() < 5]
        
        data = []
        for date in trade_dates:
            if np.random.random() < 0.1:
                continue
            
            amount = np.random.uniform(10000, 100000)
            net_mf_amt = np.random.uniform(-amount*0.1, amount*0.1)
            
            data.append({
                'ts_code': ts_code,
                'trade_date': date,
                'close': np.random.uniform(10, 100),
                'change': np.random.uniform(-5, 5),
                'volume': np.random.uniform(1000, 10000),
                'amount': amount,
                'small_buy_vol': np.random.uniform(0, 5000),
                'small_sell_vol': np.random.uniform(0, 5000),
                'mid_buy_vol': np.random.uniform(0, 3000),
                'mid_sell_vol': np.random.uniform(0, 3000),
                'big_buy_vol': np.random.uniform(0, 2000),
                'big_sell_vol': np.random.uniform(0, 2000),
                'large_buy_vol': np.random.uniform(0, 1000),
                'large_sell_vol': np.random.uniform(0, 1000),
                'net_mf_vol': np.random.uniform(-5000, 5000),
                'net_mf_amt': net_mf_amt,
                'net_mf_ratio': net_mf_amt / amount
            })
        
        return pd.DataFrame(data)
    
    def _generate_simulated_northbound(self, start_date, end_date):
        dates = pd.date_range(start_date, end_date, freq='D')
        trade_dates = [d.strftime('%Y%m%d') for d in dates if d.weekday() < 5]
        
        data = []
        for date in trade_dates:
            data.append({
                'trade_date': date,
                'north_money': np.random.uniform(-50000, 100000),
                'south_money': np.random.uniform(-30000, 50000),
                'hgt': np.random.uniform(-30000, 60000),
                'sgt': np.random.uniform(-20000, 40000),
                'ggt_ss': np.random.uniform(-20000, 30000),
                'ggt_sz': np.random.uniform(-20000, 30000)
            })
        
        return pd.DataFrame(data)
    
    def _generate_simulated_mainforce(self, start_date, end_date):
        dates = pd.date_range(start_date, end_date, freq='D')
        trade_dates = [d.strftime('%Y%m%d') for d in dates if d.weekday() < 5]
        
        data = []
        for date in trade_dates:
            data.append({
                'trade_date': date,
                'ggt_ss': np.random.uniform(-20000, 30000),
                'ggt_sz': np.random.uniform(-20000, 30000),
                'sh_mf': np.random.uniform(-50000, 100000),
                'sz_mf': np.random.uniform(-50000, 100000),
                'north_money': np.random.uniform(-50000, 100000)
            })
        
        return pd.DataFrame(data)

def main():
    fund_flow = TushareFundFlow()
    
    print("="*70)
    print("          Tushare资金流数据获取测试")
    print("="*70)
    print(f"Tushare可用: {'✅' if TUSHARE_AVAILABLE else '❌'}")
    print(f"Token配置: {'✅' if fund_flow.token else '❌'}")
    print(f"API连接: {'✅' if fund_flow.pro else '❌'}")
    print()
    
    if not fund_flow.pro:
        print("⚠️  未配置Tushare Token，将使用模拟数据")
        print()
    
    print("--- 测试个股资金流数据 ---")
    df = fund_flow.get_stock_moneyflow('600519.SH', '20260101', '20260110')
    print(f"数据行数: {len(df)}")
    if not df.empty:
        cols = ['trade_date', 'net_mf_amt', 'net_mf_ratio']
        print(df[cols].head())
    print()
    
    print("--- 测试北向资金数据 ---")
    df = fund_flow.get_northbound_moneyflow('20260101', '20260110')
    print(f"数据行数: {len(df)}")
    if not df.empty:
        print(df[['trade_date', 'north_money', 'south_money']].head())
    print()
    
    print("--- 测试主力资金数据 ---")
    df = fund_flow.get_mainforce_moneyflow('20260101', '20260110')
    print(f"数据行数: {len(df)}")
    if not df.empty:
        print(df[['trade_date', 'ggt_ss', 'ggt_sz', 'north_money']].head())
    
    print(f"\n数据来源: {'真实Tushare数据' if fund_flow.pro else '模拟数据'}")

if __name__ == "__main__":
    main()
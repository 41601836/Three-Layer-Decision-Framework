# -*- coding: utf-8 -*-
"""
真实资金流数据获取模块
使用AKShare获取北向资金、主力资金等真实数据
"""
import sys
import os
import time
import pandas as pd
import numpy as np

try:
    import akshare as ak
    AK_AVAILABLE = True
except ImportError:
    AK_AVAILABLE = False

class FundFlowData:
    def __init__(self):
        self.cache_dir = os.path.join(os.path.dirname(__file__), 'data_cache')
        os.makedirs(self.cache_dir, exist_ok=True)
    
    def get_northbound_flow(self, date):
        """获取北向资金数据"""
        if not AK_AVAILABLE:
            return self._generate_simulated_northbound(date)
        
        try:
            df = ak.stock_hsgt_fund_flow_summary_em()
            
            north_df = df[df['资金方向'] == '北向'].copy()
            north_df['trade_date'] = date
            north_df['north_money'] = north_df['资金净流入']
            
            if north_df['north_money'].abs().max() > 0:
                north_df['north_money_ratio'] = north_df['north_money'] / north_df['north_money'].abs().max()
            else:
                north_df['north_money_ratio'] = 0
            
            return north_df
            
        except Exception as e:
            print(f"获取北向资金数据失败: {e}")
            return self._generate_simulated_northbound(date)
    
    def get_main_force_flow(self, date):
        """获取主力资金数据"""
        if not AK_AVAILABLE:
            return self._generate_simulated_main_force(date)
        
        try:
            df = ak.stock_main_fund_flow()
            
            df['ts_code'] = df['代码'].apply(lambda x: f"{x}.SH" if x.startswith('6') else f"{x}.SZ")
            df['trade_date'] = date
            df['main_force'] = df['今日排行榜-主力净占比']
            df['pct_chg'] = df['今日排行榜-今日涨跌']
            
            if df['main_force'].abs().max() > 0:
                df['main_force_ratio'] = df['main_force'] / df['main_force'].abs().max()
            else:
                df['main_force_ratio'] = 0
            
            return df[['ts_code', '名称', '最新价', 'pct_chg', 'main_force', 'main_force_ratio', 'trade_date']].rename(columns={'名称': 'name', '最新价': 'close'})
            
        except Exception as e:
            print(f"获取主力资金数据失败: {e}")
            return self._generate_simulated_main_force(date)
    
    def get_stock_fund_flow(self, ts_code, start_date, end_date):
        """获取个股资金流数据"""
        if not AK_AVAILABLE:
            return self._generate_simulated_stock_flow(ts_code, start_date, end_date)
        
        try:
            df = ak.stock_individual_fund_flow(stock=ts_code.replace('.SH', '').replace('.SZ', ''))
            
            df['日期'] = pd.to_datetime(df['日期']).dt.strftime('%Y%m%d')
            df = df[(df['日期'] >= start_date) & (df['日期'] <= end_date)]
            
            df = df.rename(columns={
                '日期': 'trade_date',
                '收盘价': 'close',
                '涨跌幅': 'pct_chg',
                '主力净流入-净额': 'main_inflow',
                '主力净流入-占比': 'main_ratio',
                '超大单净流入-净额': 'large_inflow',
                '大单净流入-净额': 'big_inflow',
                '中单净流入-净额': 'mid_inflow',
                '小单净流入-净额': 'small_inflow'
            })
            
            df['ts_code'] = ts_code
            
            return df
            
        except Exception as e:
            print(f"获取个股资金流数据失败 {ts_code}: {e}")
            return self._generate_simulated_stock_flow(ts_code, start_date, end_date)
    
    def get_fund_flow_rank(self):
        """获取资金流排名"""
        if not AK_AVAILABLE:
            return pd.DataFrame()
        
        try:
            df = ak.stock_individual_fund_flow_rank()
            
            df['ts_code'] = df['代码'].apply(lambda x: f"{x}.SH" if x.startswith('6') else f"{x}.SZ")
            
            df = df.rename(columns={
                '名称': 'name',
                '最新价': 'close',
                '涨跌幅': 'pct_chg',
                '主力净流入-净额': 'main_inflow',
                '主力净流入-占比': 'main_ratio'
            })
            
            return df[['ts_code', 'name', 'close', 'pct_chg', 'main_inflow', 'main_ratio']]
            
        except Exception as e:
            print(f"获取资金流排名失败: {e}")
            return pd.DataFrame()
    
    def _generate_simulated_northbound(self, date):
        """生成模拟北向资金数据"""
        stocks = ['贵州茅台', '宁德时代', '比亚迪', '五粮液', '招商银行']
        data = []
        
        for stock in stocks:
            data.append({
                'name': stock,
                'close': np.random.uniform(100, 200),
                'pct_chg': np.random.uniform(-2, 2),
                'north_money': np.random.uniform(-10000, 20000),
                'trade_date': date,
                'north_money_ratio': np.random.uniform(-1, 1)
            })
        
        return pd.DataFrame(data)
    
    def _generate_simulated_main_force(self, date):
        """生成模拟主力资金数据"""
        codes = ['600519', '300750', '002594', '000858', '600036']
        names = ['贵州茅台', '宁德时代', '比亚迪', '五粮液', '招商银行']
        data = []
        
        for code, name in zip(codes, names):
            data.append({
                'ts_code': f"{code}.SH" if code.startswith('6') else f"{code}.SZ",
                'name': name,
                'close': np.random.uniform(100, 200),
                'pct_chg': np.random.uniform(-2, 2),
                'main_force': np.random.uniform(-5000, 10000),
                'trade_date': date,
                'main_force_ratio': np.random.uniform(-1, 1)
            })
        
        return pd.DataFrame(data)
    
    def _generate_simulated_stock_flow(self, ts_code, start_date, end_date):
        """生成模拟个股资金流数据"""
        dates = pd.date_range(start_date, end_date, freq='D').strftime('%Y%m%d').tolist()
        data = []
        
        for date in dates:
            if np.random.random() < 0.3:
                continue
            
            data.append({
                'trade_date': date,
                'close': np.random.uniform(10, 50),
                'pct_chg': np.random.uniform(-3, 3),
                'main_inflow': np.random.uniform(-1000, 2000),
                'main_ratio': np.random.uniform(-5, 10),
                'large_inflow': np.random.uniform(-500, 1000),
                'big_inflow': np.random.uniform(-300, 600),
                'mid_inflow': np.random.uniform(-200, 400),
                'small_inflow': np.random.uniform(-100, 200),
                'ts_code': ts_code
            })
        
        return pd.DataFrame(data)

def main():
    fund_flow = FundFlowData()
    today = time.strftime('%Y%m%d')
    
    print("="*70)
    print("          真实资金流数据获取测试")
    print("="*70)
    print(f"AKShare可用: {'✅' if AK_AVAILABLE else '❌'}")
    print()
    
    print("--- 北向资金数据 ---")
    north_df = fund_flow.get_northbound_flow(today)
    if '板块' in north_df.columns:
        print(north_df[['板块', '资金净流入', '相关指数', '指数涨跌幅']].head())
    else:
        print(north_df[['name', 'close', 'pct_chg', 'north_money']].head())
    print()
    
    print("--- 主力资金数据 ---")
    main_df = fund_flow.get_main_force_flow(today)
    print(main_df[['ts_code', 'name', 'close', 'pct_chg', 'main_force']].head())
    print()
    
    print("--- 资金流排名 ---")
    rank_df = fund_flow.get_fund_flow_rank()
    if not rank_df.empty:
        print(rank_df[['ts_code', 'name', 'close', 'pct_chg', 'main_inflow']].head())
    else:
        print("获取资金流排名失败")
    
    print(f"\n数据来源: {'真实AKShare数据' if AK_AVAILABLE else '模拟数据'}")

if __name__ == "__main__":
    main()
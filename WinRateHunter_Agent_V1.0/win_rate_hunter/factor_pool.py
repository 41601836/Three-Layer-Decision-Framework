# -*- coding: utf-8 -*-
"""
因子池管理器 - 管理所有可用因子及其元数据
"""
from dataclasses import dataclass
from typing import List, Dict, Any
import pandas as pd
import numpy as np

@dataclass
class Factor:
    name: str
    display_name: str
    factor_type: str  # 'price', 'volume', 'money', 'chips', 'fundamental', 'macro'
    direction: str  # 'higher_better' or 'lower_better'
    default_threshold: float
    min_value: float = -float('inf')
    max_value: float = float('inf')
    missing_rate: float = 0.0
    compute_cost: int = 1  # 1-10, 越高越耗时
    
    def compute(self, df: pd.DataFrame) -> pd.Series:
        """计算因子值"""
        if self.name in df.columns:
            return df[self.name]
        return self._compute(df)
    
    def _compute(self, df: pd.DataFrame) -> pd.Series:
        """子类实现具体计算逻辑"""
        raise NotImplementedError

class PriceFactor(Factor):
    def _compute(self, df: pd.DataFrame) -> pd.Series:
        if self.name == 'pct_chg':
            return df['pct_chg']
        elif self.name == 'ma5':
            return df.groupby('ts_code')['close'].transform(lambda x: x.rolling(5).mean())
        elif self.name == 'ma20':
            return df.groupby('ts_code')['close'].transform(lambda x: x.rolling(20).mean())
        elif self.name == 'ma60':
            return df.groupby('ts_code')['close'].transform(lambda x: x.rolling(60).mean())
        elif self.name == 'bullish':
            ma5 = df.groupby('ts_code')['close'].transform(lambda x: x.rolling(5).mean())
            ma20 = df.groupby('ts_code')['close'].transform(lambda x: x.rolling(20).mean())
            ma60 = df.groupby('ts_code')['close'].transform(lambda x: x.rolling(60).mean())
            return (ma5 > ma20) & (ma20 > ma60)
        elif self.name == 'ret_20d':
            return df.groupby('ts_code')['close'].transform(lambda x: x.pct_change(20))
        elif self.name == 'volatility_60d':
            return df.groupby('ts_code')['pct_chg'].transform(lambda x: x.rolling(60).std() / 100)
        return pd.Series(0, index=df.index)

class VolumeFactor(Factor):
    def _compute(self, df: pd.DataFrame) -> pd.Series:
        if self.name == 'vol_ma5':
            return df.groupby('ts_code')['vol'].transform(lambda x: x.rolling(5).mean())
        elif self.name == 'vol_ratio_5d':
            vol_ma5 = df.groupby('ts_code')['vol'].transform(lambda x: x.rolling(5).mean())
            return df['vol'] / vol_ma5.replace(0, np.nan)
        return pd.Series(0, index=df.index)

class MoneyFactor(Factor):
    def _compute(self, df: pd.DataFrame) -> pd.Series:
        if self.name == 'net_main_intensity':
            return df.get('net_main_intensity', pd.Series(0, index=df.index))
        return pd.Series(0, index=df.index)

class ChipsFactor(Factor):
    def _compute(self, df: pd.DataFrame) -> pd.Series:
        if self.name == 'winner_rate':
            return df.get('winner_rate', pd.Series(0, index=df.index))
        elif self.name == 'chips_peak_pct':
            return df.get('chips_peak_pct', pd.Series(0, index=df.index))
        return pd.Series(0, index=df.index)

class FundamentalFactor(Factor):
    def _compute(self, df: pd.DataFrame) -> pd.Series:
        if self.name == 'pe':
            return df.get('pe', pd.Series(100, index=df.index))
        elif self.name == 'pb':
            return df.get('pb', pd.Series(100, index=df.index))
        elif self.name == 'circ_mv_yi':
            return df.get('circ_mv_yi', pd.Series(0, index=df.index))
        return pd.Series(0, index=df.index)

class FactorPool:
    def __init__(self):
        self.factors: List[Factor] = []
        self._init_default_factors()
    
    def _init_default_factors(self):
        """初始化默认因子池（20+因子）"""
        # 量价因子
        self.factors.extend([
            PriceFactor('pct_chg', '当日涨幅', 'price', 'higher_better', 2.0, min_value=-10, max_value=10),
            PriceFactor('ret_20d', '20日收益率', 'price', 'higher_better', 0.0),
            PriceFactor('volatility_60d', '60日波动率', 'price', 'lower_better', 0.30),
            PriceFactor('bullish', '均线多头', 'price', 'higher_better', True),
            VolumeFactor('vol_ratio_5d', '量比', 'volume', 'higher_better', 1.2),
        ])
        
        # 资金因子
        self.factors.extend([
            MoneyFactor('net_main_intensity', '主力净流入强度', 'money', 'higher_better', 0.10, min_value=-1, max_value=1),
        ])
        
        # 筹码因子
        self.factors.extend([
            ChipsFactor('winner_rate', '获利盘占比', 'chips', 'higher_better', 0.70, min_value=0, max_value=1),
            ChipsFactor('chips_peak_pct', '筹码集中度', 'chips', 'higher_better', 18.0, min_value=0, max_value=100),
        ])
        
        # 基本面因子
        self.factors.extend([
            FundamentalFactor('pe', '市盈率', 'fundamental', 'lower_better', 20.0, min_value=0, max_value=100),
            FundamentalFactor('pb', '市净率', 'fundamental', 'lower_better', 3.0, min_value=0, max_value=50),
            FundamentalFactor('circ_mv_yi', '流通市值(亿)', 'fundamental', 'higher_better', 50.0, min_value=0),
        ])
    
    def get_by_type(self, factor_type: str) -> List[Factor]:
        """按类型获取因子"""
        return [f for f in self.factors if f.factor_type == factor_type]
    
    def get_random_factors(self, min_count: int = 3, max_count: int = 8) -> List[Factor]:
        """随机选择因子"""
        count = np.random.randint(min_count, max_count + 1)
        return np.random.choice(self.factors, size=count, replace=False).tolist()
    
    def get_factor_by_name(self, name: str) -> Factor:
        """按名称获取因子"""
        for f in self.factors:
            if f.name == name:
                return f
        return None
    
    def __len__(self):
        return len(self.factors)
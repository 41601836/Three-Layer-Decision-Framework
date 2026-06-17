# backend/app/core/mootdx_client.py
"""
mootdx 行情客户端封装
- 直连通达信 TCP 协议，支持全周期 K 线（分/日/周/月）
- 无需 Tushare 积分，永不封 IP
- 兼容现有 Tushare 的字段命名（close, open, high, low, vol, amount）
"""

import pandas as pd
import logging
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from mootdx.quotes import Quotes
from mootdx import consts

logger = logging.getLogger(__name__)

# 通达信代码格式转换
def ts_code_to_tdx(ts_code: str) -> str:
    """将 Tushare 代码 (000001.SZ) 转为通达信代码 (000001)"""
    return ts_code.replace('.SZ', '').replace('.SH', '').replace('.BJ', '')

def tdx_to_ts_code(tdx_code: str, market: int) -> str:
    """将通达信代码 + 市场转回 Tushare 代码"""
    if market == consts.MARKET_SH:
        return f"{tdx_code}.SH"
    elif market == consts.MARKET_SZ:
        return f"{tdx_code}.SZ"
    else:
        return f"{tdx_code}.BJ"

class MootdxClient:
    """mootdx 客户端单例"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.market = Quotes.factory(market='std')
        self._initialized = True
        logger.info("MootdxClient initialized")

    def _get_symbol(self, ts_code: str) -> tuple:
        """返回 (tdx_code, market)"""
        code = ts_code_to_tdx(ts_code)
        if 'SH' in ts_code or code.startswith('6'):
            market = consts.MARKET_SH
        elif 'SZ' in ts_code or code.startswith(('0', '3')):
            market = consts.MARKET_SZ
        else:
            market = consts.MARKET_SZ  # 默认深市
        return code, market

    def get_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取日线数据，返回字段与 Tushare 兼容
        """
        code, market = self._get_symbol(ts_code)
        try:
            df = self.market.k(symbol=code, begin=start_date, end=end_date)
            if df is None or df.empty:
                return pd.DataFrame()
            # 字段映射
            df = df.rename(columns={
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'vol',
                'amount': 'amount',
                'datetime': 'trade_date'
            })
            df['ts_code'] = ts_code
            df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '')
            cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount']
            df = df[cols]
            logger.debug(f"get_daily {ts_code}: {len(df)} rows")
            return df
        except Exception as e:
            logger.error(f"mootdx get_daily failed for {ts_code}: {e}")
            return pd.DataFrame()

    def get_weekly(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取周线数据（接口与日线相同，返回周线聚合）
        """
        code, market = self._get_symbol(ts_code)
        try:
            df = self.market.k(symbol=code, begin=start_date, end=end_date, frequency=5)
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(columns={
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'vol',
                'amount': 'amount',
                'datetime': 'trade_date'
            })
            df['ts_code'] = ts_code
            df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '')
            cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount']
            df = df[cols]
            return df
        except Exception as e:
            logger.error(f"mootdx get_weekly failed for {ts_code}: {e}")
            return pd.DataFrame()

    def get_index_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取指数日线（上证/深证）
        """
        code = '000001' if 'SH' in ts_code else '399001'
        try:
            df = self.market.index(symbol=code, begin=start_date, end=end_date)
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(columns={
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'vol',
                'amount': 'amount',
                'datetime': 'trade_date'
            })
            df['ts_code'] = ts_code
            df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '')
            cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount']
            df = df[cols]
            return df
        except Exception as e:
            logger.error(f"mootdx get_index_daily failed: {e}")
            return pd.DataFrame()

    def get_minute(self, ts_code: str, freq: int = 1) -> pd.DataFrame:
        """
        获取分钟线
        """
        code, market = self._get_symbol(ts_code)
        try:
            df = self.market.minute(symbol=code)
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(columns={
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'vol',
                'amount': 'amount',
                'datetime': 'trade_date'
            })
            df['ts_code'] = ts_code
            return df
        except Exception as e:
            logger.error(f"mootdx get_minute failed for {ts_code}: {e}")
            return pd.DataFrame()

    def get_trade_dates(self, start_date: str, end_date: str) -> list:
        """获取交易日列表（替代 pro.trade_cal）"""
        try:
            import akshare as ak
            df = ak.tool_trade_date_hist_sina()
            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y%m%d')
            dates = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]['trade_date'].tolist()
            return sorted(dates)
        except Exception as e:
            logger.warning(f"获取交易日历失败，使用简单判断: {e}")
            from datetime import datetime, timedelta
            start = datetime.strptime(start_date, '%Y%m%d')
            end = datetime.strptime(end_date, '%Y%m%d')
            dates = []
            current = start
            while current <= end:
                if current.weekday() < 5:
                    dates.append(current.strftime('%Y%m%d'))
                current += timedelta(days=1)
            return dates

# 单例实例
mootdx_client = MootdxClient()

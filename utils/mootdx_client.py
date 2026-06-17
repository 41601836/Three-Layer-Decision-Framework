# -*- coding: utf-8 -*-
"""
mootdx 行情客户端 - 替代 Tushare 日线/周线/指数
直连通达信 TCP 协议，永不封 IP
"""
import pandas as pd
import logging
from typing import Optional
from mootdx import consts as const
from mootdx.quotes import Quotes

logger = logging.getLogger(__name__)

class MootdxClient:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        # Quotes 也就是用户原意中的 Market
        self.market = Quotes.factory(market='std')
        self._initialized = True
        logger.info("MootdxClient 初始化完成")

    def _to_tdx_code(self, ts_code: str) -> tuple:
        """将 Tushare 代码转为通达信代码和市场的元组"""
        code = ts_code.replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
        if 'SH' in ts_code or code.startswith('6'):
            return code, const.MARKET_SH
        else:
            return code, const.MARKET_SZ

    def get_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取日线数据（字段与 Tushare 兼容）"""
        code, market = self._to_tdx_code(ts_code)
        try:
            # mootdx 的 k 接口使用 symbol, begin, end（通常是 yyyy-mm-dd 或者其它格式，这里可依库说明适配）
            df = self.market.k(symbol=code, begin=start_date, end=end_date)
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(columns={
                'datetime': 'trade_date',
                'volume': 'vol',
                'amount': 'amount'
            })
            df['ts_code'] = ts_code
            df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '')
            cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount']
            return df[cols]
        except Exception as e:
            logger.error(f"mootdx get_daily 失败 {ts_code}: {e}")
            return pd.DataFrame()

    def get_weekly(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取周线数据"""
        code, market = self._to_tdx_code(ts_code)
        try:
            # 使用 frequency=9 或者其它频次
            df = self.market.k(symbol=code, begin=start_date, end=end_date, frequency=5)
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(columns={
                'datetime': 'trade_date',
                'volume': 'vol',
                'amount': 'amount'
            })
            df['ts_code'] = ts_code
            df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '')
            cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount']
            return df[cols]
        except Exception as e:
            logger.error(f"mootdx get_weekly 失败 {ts_code}: {e}")
            return pd.DataFrame()

    def get_index_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取指数日线（上证/深证）"""
        index_map = {'000001.SH': '000001', '399001.SZ': '399001'}
        code = index_map.get(ts_code, '000001')
        try:
            df = self.market.index(symbol=code, begin=start_date, end=end_date)
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(columns={
                'datetime': 'trade_date',
                'volume': 'vol',
                'amount': 'amount'
            })
            df['ts_code'] = ts_code
            df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '')
            cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount']
            return df[cols]
        except Exception as e:
            logger.error(f"mootdx get_index_daily 失败: {e}")
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

mootdx = MootdxClient()

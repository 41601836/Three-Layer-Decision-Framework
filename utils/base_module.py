# -*- coding: utf-8 -*-
"""
base_module.py —— 数据源基类模块
================================

定义了所有数据源需要实现的抽象基类 BaseDataSource。
"""

from abc import ABC, abstractmethod
import pandas as pd


class BaseDataSource(ABC):
    """
    数据源抽象基类，用于规范各个数据源（如 Tushare、AkShare 等）的标准数据获取接口。
    """

    @abstractmethod
    def get_stock_basic(self) -> pd.DataFrame:
        """
        获取全市场股票基础信息列表。
        
        返回:
            pd.DataFrame: 包含股票代码、简称、上市状态等信息的 DataFrame，若无有效数据则返回空 DataFrame
        """
        pass

    @abstractmethod
    def get_trade_cal(self) -> pd.DataFrame:
        """
        获取历史交易日历。
        
        返回:
            pd.DataFrame: 包含交易日期及是否开市等信息的 DataFrame，若无有效数据则返回空 DataFrame
        """
        pass

    @abstractmethod
    def get_daily_kline(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取个股的日线K线行情数据。
        
        参数:
            symbol (str): 股票代码，如 "600519.SH"
            start_date (str): 开始日期，格式为 "YYYYMMDD"
            end_date (str): 结束日期，格式为 "YYYYMMDD"
            
        返回:
            pd.DataFrame: 日线行情数据 DataFrame，若无有效数据则返回空 DataFrame
        """
        pass

    @abstractmethod
    def get_money_flow(self, symbol: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """
        获取个股的资金流向历史数据。
        
        参数:
            symbol (str): 股票代码，如 "600519.SH"
            start_date (str): 开始日期，格式为 "YYYYMMDD"
            end_date (str): 结束日期，格式为 "YYYYMMDD"
            
        返回:
            pd.DataFrame: 个股资金流向数据 DataFrame，若无有效数据则返回空 DataFrame
        """
        pass

    @abstractmethod
    def get_board_money(self, indicator: str = "今日", sector_type: str = "行业资金流") -> pd.DataFrame:
        """
        获取板块（行业/概念等）的资金流向排名数据。
        
        参数:
            indicator (str): 统计范围，如 "今日", "5日", "10日"
            sector_type (str): 板块类型，如 "行业资金流", "概念资金流"
            
        返回:
            pd.DataFrame: 板块资金流向 DataFrame，若无有效数据则返回空 DataFrame
        """
        pass

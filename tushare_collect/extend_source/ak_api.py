# -*- coding: utf-8 -*-
"""
ak_api.py —— AkShare 数据源采集模块
===================================

提供 AkShareDataSource 类，实现 BaseDataSource 的抽象方法，
用于从 AkShare 获取股票基础数据、历史日线 K 线、个股及板块资金流向、交易日历等数据。
同时提供 get_intraday_snapshot 方法，支持盘中实时快照拉取。
"""

import time
import functools
from typing import Union, List
import pandas as pd

from config_loader import *
from utils.logger import collect_log
from utils.base_module import BaseDataSource


def retry_on_exception(max_retries: int = 3, delay: float = 1.0):
    """
    通用接口重试与异常捕获装饰器。
    
    参数:
        max_retries (int): 最大重试次数，默认 3 次。
        delay (float): 接口调用限频的基础延迟（秒），默认 1.0 秒。
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            # 检查是否启用了该数据源
            if not self.enable:
                collect_log.warning(f"⚠️ AkShare 数据源已关闭，跳过执行 [{func.__name__}] 并返回空 DataFrame。")
                return pd.DataFrame()

            method_name = func.__name__
            param_info = f"args={args}, kwargs={kwargs}"
            collect_log.info(f"🚀 开始调用 AkShare 接口 [{method_name}]，请求参数: {param_info}")

            # 进行重试循环
            for attempt in range(1, max_retries + 1):
                try:
                    # 实施请求休眠，防范接口频繁请求被封禁 IP
                    time.sleep(delay)
                    
                    df = func(self, *args, **kwargs)
                    
                    if not isinstance(df, pd.DataFrame):
                        raise ValueError(f"接口返回格式非 pandas DataFrame, 实际类型为: {type(df)}")
                    
                    collect_log.info(f"✅ 调用 AkShare 接口 [{method_name}] 成功，获取数据条数: {len(df)}")
                    return df
                except Exception as e:
                    collect_log.warning(
                        f"⚠️ 调用 AkShare 接口 [{method_name}] 失败，"
                        f"正准备进行第 {attempt}/{max_retries} 次重试。错误原因: {e}"
                    )
                    # 随着重试次数增加延长休眠时间
                    if attempt < max_retries:
                        time.sleep(delay * attempt)
                    else:
                        collect_log.error(
                            f"❌ 调用 AkShare 接口 [{method_name}] 达到最大重试次数 {max_retries}，"
                            f"请求彻底失败！异常堆栈: {e}"
                        )
            
            # 全方法安全异常捕获，失败后返回空 DataFrame，保证主程序不崩溃
            return pd.DataFrame()
        return wrapper
    return decorator


class AkShareDataSource(BaseDataSource):
    """
    AkShare 数据源实现类，继承自 BaseDataSource 抽象基类。
    """

    def __init__(self):
        """
        初始化 AkShareDataSource，读取并缓存全局配置。
        """
        self.enable = ENABLE_AKSHARE
        self.deviation_limit = DATA_DEVIATION_LIMIT
        collect_log.info(
            f"ℹ️ AkShare 数据源初始化完成。配置状态: "
            f"ENABLE_AKSHARE={self.enable}, DATA_DEVIATION_LIMIT={self.deviation_limit}"
        )

    def _parse_symbol(self, symbol: str):
        """
        私有辅助方法：将带有后缀的股票代码转换为 AkShare 兼容的纯 6 位数字代码及市场标识。
        例如: '600519.SH' -> ('600519', 'sh')
             '000001.SZ' -> ('000001', 'sz')
             '600519'    -> ('600519', 'sh') （智能推断）
        """
        symbol = symbol.strip().upper()
        if "." in symbol:
            code, suffix = symbol.split(".", 1)
            market = suffix.lower()
            return code, market
        else:
            # 根据前缀推断市场
            if symbol.startswith(('6', '9')):
                return symbol, "sh"
            elif symbol.startswith(('0', '3', '2')):
                return symbol, "sz"
            elif symbol.startswith(('8', '4')):
                return symbol, "bj"
            else:
                return symbol, "sh"

    @retry_on_exception(max_retries=3, delay=1.0)
    def get_stock_basic(self) -> pd.DataFrame:
        """
        获取全市场股票基础信息列表。
        
        返回:
            pd.DataFrame: 包含股票代码、简称等最新行情的 DataFrame。
        """
        import akshare as ak
        return ak.stock_zh_a_spot_em()

    @retry_on_exception(max_retries=3, delay=1.0)
    def get_trade_cal(self) -> pd.DataFrame:
        """
        获取历史交易日历。
        
        返回:
            pd.DataFrame: 包含所有历史交易日期的 DataFrame。
        """
        import akshare as ak
        return ak.tool_trade_date_hist_sina()

    @retry_on_exception(max_retries=3, delay=1.0)
    def get_daily_kline(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取指定股票在特定日期范围内的日线 K 线行情。
        
        参数:
            symbol (str): 股票代码，如 "600519.SH"
            start_date (str): 开始日期，格式为 "YYYYMMDD"
            end_date (str): 结束日期，格式为 "YYYYMMDD"
            
        返回:
            pd.DataFrame: 前复权的日线行情数据。
        """
        import akshare as ak
        code, _ = self._parse_symbol(symbol)
        return ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq"
        )

    @retry_on_exception(max_retries=3, delay=1.0)
    def get_money_flow(self, symbol: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """
        获取指定个股的资金流向数据（包含主力、大单、中单、小单流向占比）。
        获取近 100 个交易日数据后由本地 DataFrame 实施日期过滤。
        
        参数:
            symbol (str): 股票代码，如 "600519.SH"
            start_date (str): 开始日期，格式为 "YYYYMMDD"
            end_date (str): 结束日期，格式为 "YYYYMMDD"
            
        返回:
            pd.DataFrame: 过滤后的个股资金流向数据。
        """
        import akshare as ak
        code, market = self._parse_symbol(symbol)
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        
        if not df.empty and (start_date or end_date):
            # 将 "YYYYMMDD" 格式的日期转换为 akshare 返回的 "YYYY-MM-DD" 格式进行对比过滤
            if start_date:
                start_date_str = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
                df = df[df["日期"] >= start_date_str]
            if end_date:
                end_date_str = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"
                df = df[df["日期"] <= end_date_str]
                
        return df

    @retry_on_exception(max_retries=3, delay=1.0)
    def get_board_money(self, indicator: str = "今日", sector_type: str = "行业资金流") -> pd.DataFrame:
        """
        获取板块资金流向数据排名。
        
        参数:
            indicator (str): 时间跨度，如 "今日", "5日", "10日"
            sector_type (str): 板块分类，如 "行业资金流", "概念资金流"
            
        返回:
            pd.DataFrame: 板块排名数据。
        """
        import akshare as ak
        return ak.stock_sector_fund_flow_rank(indicator=indicator, sector_type=sector_type)

    @retry_on_exception(max_retries=3, delay=1.0)
    def get_intraday_snapshot(self, symbol: Union[str, List[str]] = None) -> pd.DataFrame:
        """
        新增专属方法：获取盘中分时行情快照，支持按个股过滤。
        
        参数:
            symbol (Union[str, List[str]]): 过滤的股票代码（单只或列表），若为 None 则返回全市场快照。
            
        返回:
            pd.DataFrame: 实时分时行情快照 DataFrame。
        """
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df.empty or symbol is None:
            return df
            
        if isinstance(symbol, str):
            symbols = [symbol]
        else:
            symbols = list(symbol)
            
        # 解析出纯 6 位数字代码
        pure_codes = [self._parse_symbol(s)[0] for s in symbols]
        
        # 过滤实时快照数据
        if "代码" in df.columns:
            df = df[df["代码"].isin(pure_codes)]
            
        return df

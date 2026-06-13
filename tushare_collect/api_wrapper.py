# -*- coding: utf-8 -*-
"""
api_wrapper.py —— Tushare 数据源接口适配模块
===========================================

封装 TushareDataSource 类，继承自 BaseDataSource。
负责接驳已有的 Tushare Pro 接口，对股票基本列表、交易日历、日线行情和个股资金流进行采集与清洗。
"""

import tushare as ts
import pandas as pd
from typing import Union, List
from utils.logger import collect_log
from utils.base_module import BaseDataSource
from config_loader import load_config


class TushareDataSource(BaseDataSource):
    """
    TushareDataSource 数据源，实现 BaseDataSource 中的所有抽象方法。
    """

    def __init__(self):
        """
        构造函数，从全局配置中加载 Tushare Token 并进行接口初始化。
        """
        config = load_config()
        self.token = config.get("api", {}).get("tushare_token", "")
        if not self.token or "在此填入" in self.token:
            collect_log.error("❌ Tushare: 未在 config.json 中配置有效的 tushare_token！")
            self.pro = None
        else:
            ts.set_token(self.token)
            self.pro = ts.pro_api()
            collect_log.info("ℹ️ TushareDataSource 初始化成功，已与 Tushare Pro 建立连接。")

    def get_stock_basic(self) -> pd.DataFrame:
        """
        获取全市场上市交易的股票基础列表。
        
        返回:
            pd.DataFrame: 股票代码及基本属性。
        """
        if not self.pro:
            collect_log.warning("⚠️ Tushare 客户端未就绪，跳过 [get_stock_basic]。")
            return pd.DataFrame()

        collect_log.info("🚀 [Tushare] 正在调取 stock_basic 行情接口...")
        try:
            # list_status="L" 代表正在上市交易的股票
            df = self.pro.stock_basic(
                list_status="L", 
                fields="ts_code,symbol,name,area,industry,market,list_date"
            )
            return df
        except Exception as e:
            collect_log.error(f"❌ [Tushare] 调取 stock_basic 接口异常: {e}")
            return pd.DataFrame()

    def get_trade_cal(self) -> pd.DataFrame:
        """
        获取历史交易日历。
        
        返回:
            pd.DataFrame: SSE 开市交易日历。
        """
        if not self.pro:
            return pd.DataFrame()

        collect_log.info("🚀 [Tushare] 正在调取 trade_cal 行情接口...")
        try:
            df = self.pro.trade_cal(exchange="SSE", is_open="1")
            return df
        except Exception as e:
            collect_log.error(f"❌ [Tushare] 调取 trade_cal 接口异常: {e}")
            return pd.DataFrame()

    def get_daily_kline(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取指定股票的日线 K 线行情。
        
        参数:
            symbol (str): 股票代码 (例如: "600519.SH")
            start_date (str): 格式 "YYYYMMDD"
            end_date (str): 格式 "YYYYMMDD"
            
        返回:
            pd.DataFrame: 日线行情。
        """
        if not self.pro:
            return pd.DataFrame()

        collect_log.info(f"🚀 [Tushare] 正在调取 daily 接口获取个股日线 -> {symbol} ({start_date} ~ {end_date})...")
        try:
            # 格式：ts_code 对应带市场后缀的代码
            df = self.pro.daily(ts_code=symbol, start_date=start_date, end_date=end_date)
            return df
        except Exception as e:
            collect_log.error(f"❌ [Tushare] 调取 daily 个股日线接口异常: {e}")
            return pd.DataFrame()

    def get_money_flow(self, symbol: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """
        获取指定股票的资金流向数据。
        
        参数:
            symbol (str): 股票代码
            start_date (str): 开始日期
            end_date (str): 结束日期
            
        返回:
            pd.DataFrame: 资金流向明细。
        """
        if not self.pro:
            return pd.DataFrame()

        collect_log.info(f"🚀 [Tushare] 正在调取 moneyflow 接口获取资金流 -> {symbol}...")
        try:
            df = self.pro.moneyflow(ts_code=symbol, start_date=start_date, end_date=end_date)
            return df
        except Exception as e:
            collect_log.error(f"❌ [Tushare] 调取 moneyflow 资金流接口异常: {e}")
            return pd.DataFrame()

    def get_board_money(self, indicator: str = "今日", sector_type: str = "行业资金流") -> pd.DataFrame:
        """
        获取板块资金流向。Tushare 未提供免费公共板块流向接口，自动安全降级。
        """
        collect_log.warning("⚠️ Tushare 不支持 [get_board_money] 板块资金流接口，返回空 DataFrame。")
        return pd.DataFrame()

    # =========================================================================
    # 专属方法的降级实现 (与其余数据源完全对齐)
    # =========================================================================

    def get_intraday_snapshot(self, symbol: Union[str, List[str]] = None) -> pd.DataFrame:
        """
        获取盘中分时行情快照。Tushare 无免费高频实时分时推送，安全降级。
        """
        collect_log.warning("⚠️ Tushare 不支持 [get_intraday_snapshot] 实时快照接口，返回空 DataFrame。")
        return pd.DataFrame()

    def get_global_macro(self) -> pd.DataFrame:
        """
        获取海外全球宏观因子。Tushare 无对应专属外盘爬取通道，安全降级。
        """
        collect_log.warning("⚠️ Tushare 不支持 [get_global_macro] 海外宏观接口，返回空 DataFrame。")
        return pd.DataFrame()

# -*- coding: utf-8 -*-
"""
data_factory.py —— 数据源统一调度工厂
====================================

对外提供全局单例 data_factory。
管理 Tushare, AkShare, Crawl 三个数据源的初始化、优先级轮询调度、连续故障降级及多源数据比对偏差校验。
"""

import pandas as pd
from typing import Union, List

from config_loader import (
    DATA_SOURCE_PRIORITY,
    AUTO_SWITCH_SOURCE,
    SOURCE_FAIL_MAX,
    DATA_DEVIATION_LIMIT
)
from utils.logger import collect_log
from utils.base_module import BaseDataSource

# 导入具体数据源
from tushare_collect.api_wrapper import TushareDataSource
from tushare_collect.extend_source.ak_api import AkShareDataSource
from tushare_collect.extend_source.crawl import CrawlDataSource


class DataFactory(BaseDataSource):
    """
    统一数据源工厂，继承自 BaseDataSource。
    负责在内部调度具体数据源，并实现接口容灾与多源校验。
    """

    def __init__(self):
        """
        初始化数据工厂，加载配置参数并实例化可用的数据源。
        """
        self.priority = DATA_SOURCE_PRIORITY
        self.auto_switch = AUTO_SWITCH_SOURCE
        self.fail_max = SOURCE_FAIL_MAX
        self.deviation_limit = DATA_DEVIATION_LIMIT

        # 连续失败计数器与临时禁用状态
        self.fail_counters = {src: 0 for src in self.priority}
        self.disabled_sources = {src: False for src in self.priority}

        # 实例化的具体数据源字典
        self.sources = {}
        for src in self.priority:
            try:
                if src == "tushare":
                    self.sources[src] = TushareDataSource()
                elif src == "akshare":
                    self.sources[src] = AkShareDataSource()
                elif src == "crawl":
                    self.sources[src] = CrawlDataSource()
            except Exception as e:
                collect_log.error(f"❌ [DataFactory] 实例化数据源 {src} 时发生异常: {e}")

        collect_log.info(
            f"ℹ️ [DataFactory] 数据源统一工厂挂载成功。优先级别: {self.priority}, "
            f"容灾切换: {self.auto_switch}, 最大重试限制: {self.fail_max}, 偏差警示阈值: {self.deviation_limit:.2%}"
        )

    def reset_counters(self):
        """
        供定时任务每日盘前调用，重置所有数据源的故障状态与计数器。
        """
        for src in self.priority:
            self.fail_counters[src] = 0
            self.disabled_sources[src] = False
        collect_log.info("🔄 [DataFactory] 数据源状态与失败计数器已全量重置。")

    def _handle_failure(self, src: str, method_name: str):
        """
        内部辅助方法：处理单次调用失败的计数与状态变更。
        """
        self.fail_counters[src] += 1
        collect_log.warning(
            f"⚠️ [DataFactory] 数据源 [{src}] 执行方法 [{method_name}] 失败! "
            f"当前连续失败计数: {self.fail_counters[src]}/{self.fail_max}"
        )
        if self.fail_counters[src] >= self.fail_max:
            self.disabled_sources[src] = True
            collect_log.error(
                f"🚨 [DataFactory] 数据源 [{src}] 连续失败已达上限 {self.fail_max}! "
                f"系统已将其置为临时禁用状态。"
            )

    def _validate_data_deviation(self, method_name: str, primary_df: pd.DataFrame, secondary_df: pd.DataFrame, ref_col: str):
        """
        内部辅助方法：对比多源返回的核心数值，如果偏差过大输出警告日志。
        """
        if primary_df.empty or secondary_df.empty:
            return

        try:
            # 取第一行的核心字段值进行对比
            p_val = float(primary_df.iloc[0][ref_col])
            s_val = float(secondary_df.iloc[0][ref_col])

            if p_val > 0:
                deviation = abs(p_val - s_val) / p_val
                if deviation > self.deviation_limit:
                    collect_log.warning(
                        f"⚠️ [数据偏差告警] 接口 [{method_name}] 的多源返回数据偏差超限! "
                        f"高优先级值: {p_val}, 次级源值: {s_val}, "
                        f"偏差比例: {deviation:.2%} (阈值: {self.deviation_limit:.2%})"
                    )
        except Exception as e:
            collect_log.debug(f"[DataFactory] 偏差计算过程被忽略: {e}")

    def _route_call(self, method_name: str, *args, **kwargs) -> pd.DataFrame:
        """
        核心调度方法：按优先级分发执行、处理计数、实现容灾自动降级与多源校验。
        """
        active_results = []

        # 1. 优先级轮询与故障隔离
        for src in self.priority:
            if self.disabled_sources.get(src, False):
                # 数据源已禁用，跳过
                continue

            datasource = self.sources.get(src)
            if not datasource:
                continue

            method = getattr(datasource, method_name, None)
            if not method:
                continue

            try:
                # 执行具体数据源数据拉取
                df = method(*args, **kwargs)

                if df is not None and not df.empty:
                    # 获取有效数据后重置失败次数，并压入活跃列表
                    self.fail_counters[src] = 0
                    active_results.append((src, df))
                    # 若关闭自动故障切换，则第一轮拿到数据后即可终止
                    if not self.auto_switch:
                        break
                else:
                    self._handle_failure(src, method_name)
                    if not self.auto_switch:
                        break
            except Exception as e:
                collect_log.error(f"❌ [DataFactory] 数据源 [{src}] 执行异常: {e}")
                self._handle_failure(src, method_name)
                if not self.auto_switch:
                    break

        if not active_results:
            collect_log.error(f"❌ [DataFactory] 所有数据源均无法为 [{method_name}] 提供有效数据!")
            return pd.DataFrame()

        # 2. 存在多源数据时的数值校验比对 (仅在取得多个数据源且有可比数值时触发)
        if len(active_results) >= 2:
            primary_df = active_results[0][1]
            secondary_df = active_results[1][1]
            
            # 智能匹配可能的核心数值字段
            ref_col = None
            for col in ["close", "最新价", "vix", "brent_price"]:
                if col in primary_df.columns and col in secondary_df.columns:
                    ref_col = col
                    break
            
            if ref_col:
                self._validate_data_deviation(method_name, primary_df, secondary_df, ref_col)

        # 3. 始终优先提供高优先级源提供的数据
        return active_results[0][1]

    # =========================================================================
    # 实现 BaseDataSource 的所有抽象方法
    # =========================================================================

    def get_stock_basic(self) -> pd.DataFrame:
        """
        获取全市场股票基本信息。
        """
        return self._route_call("get_stock_basic")

    def get_trade_cal(self) -> pd.DataFrame:
        """
        获取交易日历。
        """
        return self._route_call("get_trade_cal")

    def get_daily_kline(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取日线 K 线行情。
        """
        return self._route_call("get_daily_kline", symbol, start_date, end_date)

    def get_money_flow(self, symbol: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """
        获取个股资金流。
        """
        return self._route_call("get_money_flow", symbol, start_date, end_date)

    def get_board_money(self, indicator: str = "今日", sector_type: str = "行业资金流") -> pd.DataFrame:
        """
        获取板块资金流。
        """
        return self._route_call("get_board_money", indicator, sector_type)

    # =========================================================================
    # 实现工厂对外提供的两个专有方法 (与全部数据源对齐)
    # =========================================================================

    def get_intraday_snapshot(self, symbol: Union[str, List[str]] = None) -> pd.DataFrame:
        """
        统一接口：获取盘中实时行情快照。
        """
        return self._route_call("get_intraday_snapshot", symbol)

    def get_global_macro(self) -> pd.DataFrame:
        """
        统一接口：获取全球外围宏观数据。
        """
        return self._route_call("get_global_macro")


# 提供统一对外的全局单例对象
data_factory = DataFactory()

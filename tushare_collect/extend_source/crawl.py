# -*- coding: utf-8 -*-
"""
crawl.py —— 网页爬虫数据源模块
==============================

实现了 CrawlDataSource 类，继承自 BaseDataSource。
本模块主要作为海外宏观数据源补充，通过通用 SafeCrawler 工具拉取外围市场指标并做数据对齐。
"""

import re
import pandas as pd
from datetime import datetime
from config_loader import *
from utils.logger import collect_log
from utils.base_module import BaseDataSource
from utils.crawler import SafeCrawler


class CrawlDataSource(BaseDataSource):
    """
    CrawlDataSource 数据源，主要用于获取海外宏观核心指标。
    常规 A 股数据获取方法默认降级返回空 DataFrame 并记录日志。
    """

    def __init__(self):
        """
        初始化数据源，实例化通用爬虫组件 SafeCrawler。
        """
        self.crawler = SafeCrawler()
        collect_log.info("ℹ️ CrawlDataSource 辅助数据源初始化完成。")

    # =========================================================================
    # 基类 5 个抽象方法的降级实现
    # =========================================================================

    def get_stock_basic(self) -> pd.DataFrame:
        """
        获取股票基础信息。此数据源不支持此常规接口，直接返回空 DataFrame。
        """
        collect_log.warning("⚠️ CrawlDataSource 不支持 [get_stock_basic] 接口，返回空 DataFrame。")
        return pd.DataFrame()

    def get_trade_cal(self) -> pd.DataFrame:
        """
        获取交易日历。此数据源不支持此常规接口，直接返回空 DataFrame。
        """
        collect_log.warning("⚠️ CrawlDataSource 不支持 [get_trade_cal] 接口，返回空 DataFrame。")
        return pd.DataFrame()

    def get_daily_kline(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取日线 K 线行情。此数据源不支持此常规接口，直接返回空 DataFrame。
        """
        collect_log.warning(
            f"⚠️ CrawlDataSource 不支持 [get_daily_kline] 接口 (参数: symbol={symbol})，"
            f"返回空 DataFrame。"
        )
        return pd.DataFrame()

    def get_money_flow(self, symbol: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """
        获取个股资金流向。此数据源不支持此常规接口，直接返回空 DataFrame。
        """
        collect_log.warning(
            f"⚠️ CrawlDataSource 不支持 [get_money_flow] 接口 (参数: symbol={symbol})，"
            f"返回空 DataFrame。"
        )
        return pd.DataFrame()

    def get_board_money(self, indicator: str = "今日", sector_type: str = "行业资金流") -> pd.DataFrame:
        """
        获取板块资金流向。此数据源不支持此常规接口，直接返回空 DataFrame。
        """
        collect_log.warning("⚠️ CrawlDataSource 不支持 [get_board_money] 接口，返回空 DataFrame。")
        return pd.DataFrame()

    # =========================================================================
    # 专属海外宏观核心接口实现
    # =========================================================================

    def get_global_macro(self) -> pd.DataFrame:
        """
        专属方法：爬取外围宏观及风险指标数据，字段对齐项目 global_macro_daily 数据表结构。
        主要包括：VIX 指数、布伦特原油价及涨跌幅、美元指数、离岸人民币汇率、美股三大指数涨跌幅、韩日指数日内涨跌幅。
        
        返回:
            pd.DataFrame: 包含海外宏观指标的单行 DataFrame。
        """
        # 构建新浪批量行情请求 URL 和 Headers
        url = "https://hq.sinajs.cn/list=hf_VX,hf_OIL,DINIW,fx_susdcnh,int_dji,int_nasdaq,int_sp500,int_nikkei,b_KOSPI"
        headers = {"Referer": "http://finance.sina.com.cn"}

        collect_log.info("🚀 开始通过爬虫请求新浪全球宏观行情数据...")
        
        # 使用通用 crawler 抓取 HTML 文本
        html = self.crawler.fetch_html(url, headers=headers)
        if not html:
            collect_log.error("❌ 获取新浪全球宏观行情数据失败，网页返回为空。")
            return pd.DataFrame()

        # 解析 JS 返回数据 (格式为: var hq_str_CODE="FIELD1,FIELD2..."; )
        pattern = re.compile(r'var hq_str_(\w+)="([^"]*)";')
        matches = pattern.findall(html)

        # 整理得到的变量字典
        raw_data = {}
        for code, val_str in matches:
            if val_str:
                raw_data[code] = val_str.split(",")

        collect_log.info(f"✅ 新浪全球宏观数据下载完毕，成功获取的代码数: {len(raw_data)}")

        # 初始化宏观数据字段为 None
        vix_val = None
        brent_price_val = None
        brent_pct_val = None
        dxy_val = None
        usdcnh_val = None
        dji_pct_val = None
        ixic_pct_val = None
        spx_pct_val = None
        kospi_pct_val = None
        n225_pct_val = None
        
        # 默认使用系统当前日期 YYYYMMDD
        trade_date_val = datetime.now().strftime("%Y%m%d")

        # 1. 解析 VIX 恐慌指数期货 (hf_VX) -> 最新价在 fields[0]
        if "hf_VX" in raw_data:
            try:
                vix_val = float(raw_data["hf_VX"][0])
            except (ValueError, IndexError) as e:
                collect_log.warning(f"⚠️ 解析 VIX 指数出错: {e}")

        # 2. 解析布伦特原油 (hf_OIL) -> 最新价在 fields[0], 昨收在 fields[7]
        if "hf_OIL" in raw_data:
            try:
                brent_price_val = float(raw_data["hf_OIL"][0])
                brent_prev_close = float(raw_data["hf_OIL"][7])
                if brent_prev_close > 0:
                    brent_pct_val = (brent_price_val - brent_prev_close) / brent_prev_close * 100
            except (ValueError, IndexError) as e:
                collect_log.warning(f"⚠️ 解析布伦特原油数据出错: {e}")

        # 3. 解析美元指数 (DINIW) -> 最新价在 fields[1]
        if "DINIW" in raw_data:
            try:
                dxy_val = float(raw_data["DINIW"][1])
            except (ValueError, IndexError) as e:
                collect_log.warning(f"⚠️ 解析美元指数出错: {e}")

        # 4. 解析离岸人民币 (fx_susdcnh) -> 最新价在 fields[1], 日期在 fields[-1] (如 2026-06-13)
        if "fx_susdcnh" in raw_data:
            try:
                fields = raw_data["fx_susdcnh"]
                usdcnh_val = float(fields[1])
                # 尝试用外汇数据的真实交易日作为该批数据的 trade_date
                date_str = fields[-1].replace("-", "").strip()
                if len(date_str) == 8 and date_str.isdigit():
                    trade_date_val = date_str
            except (ValueError, IndexError) as e:
                collect_log.warning(f"⚠️ 解析离岸人民币数据出错: {e}")

        # 5. 解析美股三大指数涨跌幅 (int_dji, int_nasdaq, int_sp500) -> 涨跌幅在 fields[3] (%)
        if "int_dji" in raw_data:
            try:
                dji_pct_val = float(raw_data["int_dji"][3])
            except (ValueError, IndexError) as e:
                collect_log.warning(f"⚠️ 解析道琼斯指数涨跌幅出错: {e}")

        if "int_nasdaq" in raw_data:
            try:
                ixic_pct_val = float(raw_data["int_nasdaq"][3])
            except (ValueError, IndexError) as e:
                collect_log.warning(f"⚠️ 解析纳斯达克指数涨跌幅出错: {e}")

        if "int_sp500" in raw_data:
            try:
                spx_pct_val = float(raw_data["int_sp500"][3])
            except (ValueError, IndexError) as e:
                collect_log.warning(f"⚠️ 解析标普500指数涨跌幅出错: {e}")

        # 6. 解析日经 225 涨跌幅 (int_nikkei) -> 涨跌幅在 fields[3] (%)
        if "int_nikkei" in raw_data:
            try:
                n225_pct_val = float(raw_data["int_nikkei"][3])
            except (ValueError, IndexError) as e:
                collect_log.warning(f"⚠️ 解析日经225指数涨跌幅出错: {e}")

        # 7. 解析韩国 KOSPI 涨跌幅 (b_KOSPI) -> 涨跌幅在 fields[3] (%)
        if "b_KOSPI" in raw_data:
            try:
                kospi_pct_val = float(raw_data["b_KOSPI"][3])
            except (ValueError, IndexError) as e:
                collect_log.warning(f"⚠️ 解析韩国KOSPI指数涨跌幅出错: {e}")

        # 整合为对齐的数据行 DataFrame
        macro_record = {
            "trade_date": trade_date_val,
            "vix": vix_val,
            "brent_price": brent_price_val,
            "brent_pct": brent_pct_val,
            "dxy": dxy_val,
            "usdcnh": usdcnh_val,
            "dji_pct": dji_pct_val,
            "ixic_pct": ixic_pct_val,
            "spx_pct": spx_pct_val,
            "kospi_pct": kospi_pct_val,
            "n225_pct": n225_pct_val
        }

        # 数据清洗过滤与分级日志记录
        missing_fields = [k for k, v in macro_record.items() if v is None]
        if missing_fields:
            collect_log.warning(f"⚠️ 宏观数据爬取完成，但部分指标缺失: {missing_fields}")
        else:
            collect_log.info("✅ 宏观指标完整抓取并完成清洗对齐。")

        df = pd.DataFrame([macro_record])
        return df

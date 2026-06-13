# -*- coding: utf-8 -*-
"""
crawler.py —— 通用网页爬虫工具模块
==================================

本模块封装了通用的网页 HTTP 请求及 HTML 解析能力。
提供了基于自适应限频休眠、自动重试和全方法异常捕获的 SafeCrawler 类，
支持安全地抓取网页 HTML 文本及生成 BeautifulSoup 对象。
"""

import time
import requests
from bs4 import BeautifulSoup

from config_loader import CRAWL_INTERVAL, CRAWL_TIMEOUT, CRAWL_USER_AGENT
from utils.logger import collect_log


class SafeCrawler:
    """
    通用安全网页爬虫工具类，内置请求频率控制、超时、重试、UA统一管理及防崩溃异常捕获。
    """

    def __init__(self):
        """
        初始化 SafeCrawler，加载配置中的限频时间、超时和 User-Agent。
        """
        self.interval = CRAWL_INTERVAL
        self.timeout = CRAWL_TIMEOUT
        self.user_agent = CRAWL_USER_AGENT
        self._last_request_time = 0.0  # 记录上一次发起请求的绝对时间戳
        collect_log.info(
            f"ℹ️ SafeCrawler 初始化完成。限频间隔: {self.interval}秒, "
            f"超时时间: {self.timeout}秒, UA: {self.user_agent}"
        )

    def _wait_for_interval(self):
        """
        自适应请求限频控制。
        计算当前距离上一次请求已过去的时间，若小于设定的最小请求间隔，则自动休眠等待差值。
        """
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.interval:
            wait_time = self.interval - elapsed
            collect_log.info(f"⏳ 触发爬虫频率限制，自适应休眠等待 {wait_time:.2f} 秒...")
            time.sleep(wait_time)
        # 记录本次发起请求的时间戳
        self._last_request_time = time.time()

    def fetch_html(self, url: str, params: dict = None, headers: dict = None, max_retries: int = 3) -> str:
        """
        通用的网页 GET 请求方法，自动包含重试、限频与异常捕获。
        
        参数:
            url (str): 目标网页 URL。
            params (dict): URL 查询参数。
            headers (dict): 额外的 HTTP 请求头。
            max_retries (int): 出现超时或网络异常时的最大重试次数。
            
        返回:
            str: 网页 HTML 文本内容，若请求彻底失败则返回空字符串 ""。
        """
        # 合并默认的 User-Agent
        req_headers = {"User-Agent": self.user_agent}
        if headers:
            req_headers.update(headers)

        for attempt in range(1, max_retries + 1):
            # 发起请求前执行自适应限频等待
            self._wait_for_interval()
            
            collect_log.info(
                f"🚀 [GET] 正在发送网页请求 ({attempt}/{max_retries}) -> URL: {url}, "
                f"参数: {params}"
            )
            
            try:
                # 执行网络请求
                response = requests.get(
                    url, 
                    params=params, 
                    headers=req_headers, 
                    timeout=self.timeout
                )
                # 检查 HTTP 状态码是否为 200 系列
                response.raise_for_status()
                # 自动识别编码格式，防止中文网页乱码
                response.encoding = response.apparent_encoding
                
                collect_log.info(f"✅ 请求成功! 响应状态码: {response.status_code}")
                return response.text
            except requests.RequestException as e:
                collect_log.warning(
                    f"⚠️ 请求发生网络异常 (尝试 {attempt}/{max_retries}) -> url: {url}, 错误: {e}"
                )
                # 发生错误时，如果未到上限，稍微加大间隔进行退避
                if attempt < max_retries:
                    time.sleep(self.interval * attempt)
                else:
                    collect_log.error(
                        f"❌ 网页请求达到最大重试次数 {max_retries}，请求失败! "
                        f"目标: {url}, 异常信息: {e}"
                    )
                    
        # 异常兜底，不引发崩溃，返回空字符串
        return ""

    def fetch_soup(self, url: str, params: dict = None, headers: dict = None, max_retries: int = 3) -> BeautifulSoup:
        """
        通用的获取网页 HTML 并直接解析为 BeautifulSoup 对象的辅助方法。
        
        参数:
            url (str): 目标网页 URL。
            params (dict): URL 查询参数。
            headers (dict): 额外的 HTTP 请求头。
            max_retries (int): 最大重试次数。
            
        返回:
            BeautifulSoup: 解析后的 BeautifulSoup 对象。若获取 HTML 失败或解析失败，
                           则返回一个不包含任何节点的空 BeautifulSoup 对象。
        """
        html = self.fetch_html(url, params=params, headers=headers, max_retries=max_retries)
        if not html:
            return BeautifulSoup("", "html.parser")

        try:
            # 使用 Python 内置的 html.parser 规避依赖冲突
            soup = BeautifulSoup(html, "html.parser")
            return soup
        except Exception as e:
            # 捕获可能的 BeautifulSoup 解析异常
            collect_log.error(f"❌ HTML 解析为 BeautifulSoup 时发生异常: {e}")
            return BeautifulSoup("", "html.parser")

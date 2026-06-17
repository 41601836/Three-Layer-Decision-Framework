# -*- coding: utf-8 -*-
"""
同花顺热点题材客户端
提供：个股题材标签（替代 Tushare concept）、当日强势股列表、板块实时热度
"""
import logging
import requests
from typing import List, Dict, Optional
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 请求头（模拟浏览器避免 403）
_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Referer': 'http://q.10jqka.com.cn/',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}
_TIMEOUT = 6


def get_stock_themes(ts_code: str) -> List[str]:
    """
    获取个股题材标签（同花顺）
    替代 Tushare pro.concept_detail() 的低频粗粒度方案
    """
    code = ts_code.replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
    url = f"http://basic.10jqka.com.cn/{code}/concept.html"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            logger.warning(f"同花顺题材请求失败 {ts_code}: HTTP {resp.status_code}")
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        # 题材列表常见 class: m-concept-box a
        tags = soup.select('a.concept-tag') or soup.select('.plate-list a') or soup.select('.m-concept li a')
        if tags:
            return [t.text.strip() for t in tags if t.text.strip()]
        # 备用解析: 任何含"板块"的 <a>
        tags = [a for a in soup.find_all('a') if '板块' in a.get('href', '') or '概念' in a.text]
        return [t.text.strip() for t in tags[:30]]
    except Exception as e:
        logger.warning(f"get_stock_themes 失败 [{ts_code}]: {e}")
        return []


def get_hot_stocks(limit: int = 30) -> List[Dict]:
    """
    获取当日同花顺热门强势股列表（资金抢筹方向）
    字段: ts_code, name, pct_chg, amount, concept
    """
    url = "http://q.10jqka.com.cn/index/index/board/all/field/zxz/order/desc/page/1/ajax/1/"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            logger.warning(f"同花顺热股请求失败: HTTP {resp.status_code}")
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        rows = soup.select('table.m-table tbody tr')
        result = []
        for row in rows[:limit]:
            cells = row.select('td')
            if len(cells) < 7:
                continue
            try:
                code = cells[1].text.strip()
                name = cells[2].text.strip()
                pct = cells[3].text.strip().replace('%', '')
                result.append({
                    'ts_code': _guess_ts_code(code),
                    'name': name,
                    'pct_chg': float(pct) if pct else None,
                })
            except Exception:
                continue
        return result
    except Exception as e:
        logger.warning(f"get_hot_stocks 失败: {e}")
        return []


def get_sector_hot_ranking(limit: int = 50) -> List[Dict]:
    """
    获取同花顺板块热度排行（替代 Tushare 概念映射的实时数据）
    字段: sector_name, pct_chg, rise_count, limit_up_count
    """
    url = "http://q.10jqka.com.cn/thshy/index/field/199112/order/desc/page/1/ajax/1/"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        rows = soup.select('table.m-table tbody tr')
        result = []
        for row in rows[:limit]:
            cells = row.select('td')
            if len(cells) < 6:
                continue
            try:
                name = cells[2].text.strip()
                pct = cells[3].text.strip().replace('%', '')
                result.append({
                    'sector_name': name,
                    'pct_chg': float(pct) if pct else 0.0,
                })
            except Exception:
                continue
        return result
    except Exception as e:
        logger.warning(f"get_sector_hot_ranking 失败: {e}")
        return []


def _guess_ts_code(code: str) -> str:
    """根据股票代码前缀猜测市场后缀"""
    if code.startswith('6'):
        return f"{code}.SH"
    elif code.startswith(('4', '8')):
        return f"{code}.BJ"
    else:
        return f"{code}.SZ"

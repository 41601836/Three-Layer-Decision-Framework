# -*- coding: utf-8 -*-
"""
腾讯财经估值接口 - 替代 Tushare daily_basic 的 PE/PB/市值/换手率
零安装，秒级响应，永不封 IP
"""
import requests
import re
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

def get_stock_valuation(ts_code: str) -> Dict[str, Optional[float]]:
    """
    获取个股 PE、PB、市值、换手率
    :param ts_code: '000001.SZ'
    :return: {'pe': float, 'pb': float, 'market_cap': float, 'turnover': float}
    """
    code = ts_code.replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
    prefix = 'sh' if 'SH' in ts_code else ('sz' if 'SZ' in ts_code else 'bj')
    url = f"http://qt.gtimg.cn/q={prefix}{code}"

    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code != 200:
            return {}
        data = resp.text
        fields = data.split('~')
        if len(fields) < 60:
            return {}

        result = {}
        try:
            result['pe'] = float(fields[38]) if fields[38] else None
        except (ValueError, IndexError):
            result['pe'] = None
        try:
            result['pb'] = float(fields[46]) if fields[46] else None
        except (ValueError, IndexError):
            result['pb'] = None
        try:
            # 返回的是亿，转换成万元与 Tushare 保持一致
            result['market_cap'] = float(fields[44]) * 10000 if fields[44] else None
        except (ValueError, IndexError):
            result['market_cap'] = None
        try:
            result['turnover'] = float(fields[52]) if fields[52] else None
        except (ValueError, IndexError):
            result['turnover'] = None

        return result
    except Exception as e:
        logger.warning(f"腾讯估值获取失败 {ts_code}: {e}")
        return {}

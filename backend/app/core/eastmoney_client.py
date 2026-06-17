# -*- coding: utf-8 -*-
"""
东财研报摘要客户端 + iwencai 语义检索
为 AI 解读功能提供机构观点支撑，并支持主题量化选股
"""
import json
import logging
import re
import requests
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

_TIMEOUT = 8
_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
}


# ════════════════════════════════════════════════════════════════════════════
# 东财研报摘要
# ════════════════════════════════════════════════════════════════════════════
def fetch_research_summary(ts_code: str, limit: int = 3) -> str:
    """
    获取最近 N 篇研报摘要（东财免费接口）
    :return: 格式化文本，用于注入 AI 解读 Prompt
    """
    code = ts_code[:6]
    url = (
        f"http://datacenter-web.eastmoney.com/api/data/v1/get"
        f"?reportName=RPT_RESEARCH_REPORT_NEW"
        f"&columns=SECURITY_CODE,ORGAN_NAME,TITLE,RATING_NAME,EMRATING_NAME,REPORT_DATE,RESEARCHER"
        f"&filter=(SECURITY_CODE%3D%22{code}%22)"
        f"&pageSize={limit}&pageNumber=1"
        f"&client=web&source=WEB"
    )
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            logger.warning(f"东财研报请求失败 {ts_code}: HTTP {resp.status_code}")
            return ""
        data = resp.json()
        items = (data.get('result') or {}).get('data', [])
        if not items:
            return ""
        lines = []
        for item in items:
            date = item.get('REPORT_DATE', '')[:10]
            organ = item.get('ORGAN_NAME', '')
            title = item.get('TITLE', '')
            rating = item.get('RATING_NAME', '') or item.get('EMRATING_NAME', '')
            researcher = item.get('RESEARCHER', '')
            lines.append(f"• [{date}] {organ} | {title} | 评级:{rating} | 分析师:{researcher}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"fetch_research_summary 失败 [{ts_code}]: {e}")
        return ""


def fetch_target_price(ts_code: str) -> Optional[float]:
    """
    获取机构平均目标价（东财一致预期接口）
    """
    code = ts_code[:6]
    url = (
        f"http://datacenter-web.eastmoney.com/api/data/v1/get"
        f"?reportName=RPT_ANALYST_ESTIMATE"
        f"&columns=SECURITY_CODE,ALL_INSTITUTION_TARGET_PRICE"
        f"&filter=(SECURITY_CODE%3D%22{code}%22)"
        f"&pageSize=1&pageNumber=1&client=web&source=WEB"
    )
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return None
        data = resp.json()
        items = (data.get('result') or {}).get('data', [])
        if items and items[0].get('ALL_INSTITUTION_TARGET_PRICE'):
            return float(items[0]['ALL_INSTITUTION_TARGET_PRICE'])
    except Exception as e:
        logger.warning(f"fetch_target_price 失败 [{ts_code}]: {e}")
    return None


# ════════════════════════════════════════════════════════════════════════════
# iwencai 语义检索
# ════════════════════════════════════════════════════════════════════════════
def search_themes(keyword: str, limit: int = 30) -> List[Dict]:
    """
    iwencai 语义检索：输入主题关键词，返回匹配个股列表
    用法示例：search_themes("人形机器人 丝杠")
    """
    url = "http://www.iwencai.com/unifiedwap/unified-wap/v2/result/get-query-result"
    payload = {
        "version": "2.0.0",
        "query": keyword,
        "query_area": "",
        "block_list": "",
        "add_info": json.dumps({"urp": {"situation": 1, "timeliness": 1, "stock_market": "ab"}}),
        "rsh": "",
        "perpage": limit,
        "page": 1,
        "source": "Ths_iwencai_Pc",
        "secondary_intent": "stock",
    }
    headers = {**_HEADERS, 'Content-Type': 'application/x-www-form-urlencoded'}
    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=_TIMEOUT)
        if resp.status_code != 200:
            logger.warning(f"iwencai 检索失败: HTTP {resp.status_code}")
            return []
        raw = resp.json()
        data = raw.get('data', {})
        # 解析股票列表（iwencai 返回结构较复杂）
        stocks = []
        answer = data.get('answer', {})
        if isinstance(answer, dict):
            for k, v in answer.items():
                if 'stock_list' in str(k).lower() or 'result' in str(k).lower():
                    if isinstance(v, list):
                        for item in v[:limit]:
                            stocks.append({
                                'ts_code': item.get('code', '') + '.SH' if item.get('code', '').startswith('6') else item.get('code', '') + '.SZ',
                                'name': item.get('name', ''),
                            })
        return stocks
    except Exception as e:
        logger.warning(f"search_themes 失败 [{keyword}]: {e}")
        return []

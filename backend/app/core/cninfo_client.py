# -*- coding: utf-8 -*-
"""
巨潮资讯公告客户端
获取近期公告：减持/解禁/立案/定增等关键风险信号
用于周一战法风险否决层
"""
import logging
import requests
from typing import List, Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_TIMEOUT = 8
_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Referer': 'http://www.cninfo.com.cn/',
}

# 风险关键词（触发则返回 True）
RISK_KEYWORDS = ['减持', '立案', '限售', '解禁', '质押', '问询', '违规', '处罚', '退市', '重大事项']


def get_announcements(ts_code: str, days: int = 7) -> List[Dict]:
    """
    获取个股近期公告列表（巨潮资讯）
    :param ts_code:  '000001.SZ'
    :param days:     往前查询天数
    :return: [{'title': str, 'date': str, 'url': str}]
    """
    code = ts_code[:6]
    market = 'sh' if 'SH' in ts_code else 'sz'
    # 巨潮 API：公告全文搜索
    from_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    to_date = datetime.now().strftime('%Y-%m-%d')

    url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
    payload = {
        'stock': f"{code},{market.upper()}{code}",
        'tabName': 'fulltext',
        'pageSize': 20,
        'pageNum': 1,
        'column': 'sse' if market == 'sh' else 'szse',
        'category': '',
        'plate': '',
        'seDate': f"{from_date}~{to_date}",
        'searchkey': '',
        'secid': '',
        'sortName': 'time',
        'sortType': 'desc',
        'isHLtitle': 'true',
    }
    try:
        resp = requests.post(url, data=payload, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            logger.warning(f"巨潮公告请求失败 {ts_code}: HTTP {resp.status_code}")
            return []
        data = resp.json()
        announcements = data.get('announcements') or []
        result = []
        for ann in announcements:
            title = ann.get('announcementTitle', '')
            date = ann.get('announcementTime', '')
            aid = ann.get('announcementId', '')
            result.append({
                'title': title,
                'date': date[:10] if date else '',
                'url': f"http://static.cninfo.com.cn/{aid}" if aid else '',
            })
        return result
    except Exception as e:
        logger.warning(f"get_announcements 失败 [{ts_code}]: {e}")
        return []


def check_risk_announcements(ts_code: str, days: int = 7) -> Dict:
    """
    风险公告检测：检查是否存在高风险类型公告
    :return: {'has_risk': bool, 'risk_titles': List[str], 'all_count': int}
    """
    anns = get_announcements(ts_code, days)
    risk_titles = []
    for ann in anns:
        title = ann.get('title', '')
        if any(kw in title for kw in RISK_KEYWORDS):
            risk_titles.append(title)
    return {
        'has_risk': len(risk_titles) > 0,
        'risk_titles': risk_titles,
        'all_count': len(anns),
    }

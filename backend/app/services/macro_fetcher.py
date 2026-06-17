# -*- coding: utf-8 -*-
"""
宏观数据采集服务 (Phase 1 - Task 1)
负责调用 AkShare 客户端拉取海外与国内宏观数据，并持久化到 macro_cache 表。
"""
import logging
from datetime import datetime
from typing import Dict, Any

from app.core.akshare_client import fetch_overseas_macro, fetch_domestic_macro
from app.dao.macro_dao import upsert_macro

logger = logging.getLogger(__name__)

# 指标分类映射：(akshare_key -> (macro_type, indicator_name))
OVERSEAS_FIELDS = {
    'spx':    ('overseas', 'SPX'),
    'vix':    ('overseas', 'VIX'),
    'usdcny': ('overseas', 'USDCNY'),
    'brent':  ('overseas', 'BRENT'),
}

DOMESTIC_FIELDS = {
    'pmi':           ('domestic', 'PMI'),
    'cpi':           ('domestic', 'CPI'),
    'ppi':           ('domestic', 'PPI'),
    'social_finance': ('domestic', 'SOCIAL_FINANCE'),
}


def fetch_all_macro() -> Dict[str, Any]:
    """
    抓取全量宏观数据（海外+国内），写入 macro_cache，返回完整结果字典。
    若某单项失败，优雅降级为 None，不影响其他项。
    """
    today_str = datetime.now().strftime('%Y-%m-%d')
    result = {'overseas': {}, 'domestic': {}, 'status': 'success'}

    # ─── 1. 海外宏观 ───────────────────────────────────────────────────────────
    logger.info("📡 开始抓取海外宏观数据...")
    try:
        overseas_raw = fetch_overseas_macro()
    except Exception as e:
        logger.error(f"fetch_overseas_macro 整体失败: {e}")
        overseas_raw = {}

    for key, (macro_type, indicator_name) in OVERSEAS_FIELDS.items():
        val = overseas_raw.get(key)
        result['overseas'][key] = val
        if val is not None:
            try:
                upsert_macro(macro_type, indicator_name, str(val), today_str, source='akshare')
            except Exception as e:
                logger.warning(f"写入 macro_cache 失败 [{indicator_name}]: {e}")
        else:
            logger.warning(f"⚠️ 海外指标 [{indicator_name}] 获取为 None，跳过入库。")

    # ─── 2. 国内宏观 ───────────────────────────────────────────────────────────
    logger.info("📡 开始抓取国内宏观数据...")
    try:
        domestic_raw = fetch_domestic_macro()
    except Exception as e:
        logger.error(f"fetch_domestic_macro 整体失败: {e}")
        domestic_raw = {}

    for key, (macro_type, indicator_name) in DOMESTIC_FIELDS.items():
        val = domestic_raw.get(key)
        result['domestic'][key] = val
        if val is not None:
            try:
                upsert_macro(macro_type, indicator_name, str(val), today_str, source='akshare')
            except Exception as e:
                logger.warning(f"写入 macro_cache 失败 [{indicator_name}]: {e}")
        else:
            logger.warning(f"⚠️ 国内指标 [{indicator_name}] 获取为 None，跳过入库。")

    logger.info(f"✅ 宏观数据抓取完成: 海外={result['overseas']}, 国内={result['domestic']}")
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = fetch_all_macro()
    print("海外宏观:", out['overseas'])
    print("国内宏观:", out['domestic'])

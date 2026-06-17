# -*- coding: utf-8 -*-
"""
统一数据源注册表 (DataSource Router)
按优先级自动路由：mootdx > 腾讯财经 > AkShare > Tushare > 本地缓存
"""
import logging
from typing import Optional, List, Dict, Any
import pandas as pd

logger = logging.getLogger(__name__)


class DataSourceRouter:
    """
    数据源路由器 - 统一入口，自动按优先级降级
    
    优先级矩阵：
    ┌─────────────────┬──────────────┬───────────────┐
    │ 数据类型         │ 首选          │ 备用           │
    ├─────────────────┼──────────────┼───────────────┤
    │ 日线行情         │ 本地DB       │ mootdx        │
    │ 指数行情         │ 本地DB       │ mootdx        │
    │ 估值指标         │ 腾讯财经      │ AkShare       │
    │ 概念板块         │ 同花顺热点    │ 本地缓存       │
    │ 宏观数据         │ AkShare      │ 本地缓存       │
    │ 研报摘要         │ 东财          │ 无            │
    │ 公告风险         │ 巨潮          │ 无            │
    └─────────────────┴──────────────┴───────────────┘
    """

    def get_daily(self, ts_code: str, start_date: str, end_date: str,
                  prefer_local: bool = True) -> pd.DataFrame:
        """
        获取日线数据 (优先本地DB → mootdx)
        """
        if prefer_local:
            try:
                from app.core.database import get_db_conn
                conn = get_db_conn()
                rows = conn.execute(
                    "SELECT * FROM daily_prices WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
                    (ts_code, start_date, end_date)
                ).fetchall()
                conn.close()
                if rows:
                    logger.debug(f"[日线] 本地DB命中: {ts_code}")
                    return pd.DataFrame([dict(r) for r in rows])
            except Exception as e:
                logger.warning(f"[日线] 本地DB查询失败: {e}")

        # mootdx 降级
        try:
            from app.core.mootdx_client import MootdxClient
            mx = MootdxClient()
            df = mx.get_daily(ts_code, start_date, end_date)
            if not df.empty:
                logger.debug(f"[日线] mootdx 命中: {ts_code}")
                return df
        except Exception as e:
            logger.warning(f"[日线] mootdx 失败: {e}")

        logger.warning(f"[日线] 全部数据源失败: {ts_code}")
        return pd.DataFrame()

    def get_index_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取指数日线 (本地DB → mootdx → Tushare)
        """
        try:
            from app.core.database import get_db_conn
            conn = get_db_conn()
            rows = conn.execute(
                "SELECT * FROM daily_index WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
                (ts_code, start_date, end_date)
            ).fetchall()
            conn.close()
            if rows:
                logger.debug(f"[指数] 本地DB命中: {ts_code}")
                return pd.DataFrame([dict(r) for r in rows])
        except Exception as e:
            logger.warning(f"[指数] 本地DB失败: {e}")

        try:
            from app.core.mootdx_client import MootdxClient
            df = MootdxClient().get_index_daily(ts_code, start_date, end_date)
            if not df.empty:
                logger.debug(f"[指数] mootdx 命中: {ts_code}")
                return df
        except Exception as e:
            logger.warning(f"[指数] mootdx 失败: {e}")

        return pd.DataFrame()

    def get_valuation(self, ts_code: str) -> Dict[str, Optional[float]]:
        """
        获取估值指标 (腾讯财经 → AkShare)
        """
        try:
            from app.core.tencent_client import get_stock_valuation
            val = get_stock_valuation(ts_code)
            if val and any(v is not None for v in val.values()):
                logger.debug(f"[估值] 腾讯财经命中: {ts_code}")
                return val
        except Exception as e:
            logger.warning(f"[估值] 腾讯财经失败: {e}")

        # AkShare 降级（获取 PE/PB）
        try:
            import akshare as ak
            code = ts_code[:6]
            df = ak.stock_individual_info_em(symbol=code)
            if df is not None and not df.empty:
                info = dict(zip(df.iloc[:, 0], df.iloc[:, 1]))
                return {
                    'pe': _try_float(info.get('市盈率(动)')),
                    'pb': _try_float(info.get('市净率')),
                    'market_cap': _try_float(info.get('总市值')),
                    'turnover': None,
                }
        except Exception as e:
            logger.warning(f"[估值] AkShare 失败: {e}")

        return {}

    def get_sector_themes(self, ts_code: str) -> List[str]:
        """
        获取题材标签 (同花顺)
        """
        try:
            from app.core.ths_hot_client import get_stock_themes
            themes = get_stock_themes(ts_code)
            if themes:
                return themes
        except Exception as e:
            logger.warning(f"[题材] 同花顺失败: {e}")
        return []

    def get_research(self, ts_code: str, limit: int = 3) -> str:
        """
        获取研报摘要 (东财)
        """
        try:
            from app.core.eastmoney_client import fetch_research_summary
            return fetch_research_summary(ts_code, limit)
        except Exception as e:
            logger.warning(f"[研报] 东财失败: {e}")
        return ""

    def check_announcements(self, ts_code: str, days: int = 7) -> Dict:
        """
        风险公告检测 (巨潮)
        """
        try:
            from app.core.cninfo_client import check_risk_announcements
            return check_risk_announcements(ts_code, days)
        except Exception as e:
            logger.warning(f"[公告] 巨潮失败: {e}")
        return {'has_risk': False, 'risk_titles': [], 'all_count': 0}


def _try_float(val) -> Optional[float]:
    try:
        return float(str(val).replace(',', '').replace('%', ''))
    except Exception:
        return None


# 全局单例
datasource = DataSourceRouter()

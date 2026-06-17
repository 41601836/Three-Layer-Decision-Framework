# -*- coding: utf-8 -*-
"""
第一层诊断引擎 (Phase 1 - Task 1 & 2)
实现六维度评分、一票否决、操作模式判定、板块初筛及完整诊断流程。
"""
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# ─── 动态导入，避免模块间循环引用 ─────────────────────────────────────────────
def _get_pro():
    from app.core.tushare_client import get_pro
    return get_pro()

def _fetch_with_retry(func, **kwargs):
    from app.core.tushare_client import fetch_with_retry
    return fetch_with_retry(func, **kwargs)

def _get_db():
    from app.core.database import get_db_conn
    return get_db_conn()

def _get_mootdx():
    """获取 mootdx 客户端（优先从 backend/app/core 加载，备用 utils）"""
    try:
        from app.core.mootdx_client import MootdxClient
        return MootdxClient()
    except ImportError:
        from utils.mootdx_client import mootdx
        return mootdx


# ════════════════════════════════════════════════════════════════════════════
# Task 1.1  资金结构数据
# ════════════════════════════════════════════════════════════════════════════
def get_funds_data(trade_date: str = None) -> Dict[str, Any]:
    """
    获取资金结构数据：两市成交额、北向资金净流入、两融余额
    """
    if not trade_date:
        trade_date = datetime.now().strftime('%Y%m%d')

    result = {
        'total_amount': None,     # 两市总成交额（亿元）
        'north_net_inflow': None, # 北向资金净流入（亿元）
        'margin_balance': None,   # 两融余额（亿元）
        'trade_date': trade_date,
    }

    try:
        pro = _get_pro()

        # 两市成交额：优先本地 daily_prices，降级腾讯财经指数数据
        try:
            conn = _get_db()
            row = conn.execute(
                "SELECT SUM(amount)/100000000.0 as total_amount FROM daily_prices WHERE trade_date=?",
                (trade_date,)
            ).fetchone()
            conn.close()
            if row and row['total_amount']:
                result['total_amount'] = round(row['total_amount'], 2)
        except Exception as e:
            logger.warning(f"本地成交额汇总失败，尝试 mootdx 指数成交: {e}")
            try:
                mx = _get_mootdx()
                # 从上证/深证指数 amount 字段推算（单只指数代表两市整体）
                df_idx = mx.get_index_daily('000001.SH', trade_date, trade_date)
                if not df_idx.empty and 'amount' in df_idx.columns:
                    result['total_amount'] = round(float(df_idx['amount'].iloc[0]) / 1e8, 2)
            except Exception as e2:
                logger.warning(f"mootdx 成交额降级也失败: {e2}")

        # 北向资金净流入
        try:
            df_hsgt = _fetch_with_retry(pro.moneyflow_hsgt, start_date=trade_date, end_date=trade_date)
            if df_hsgt is not None and not df_hsgt.empty:
                # 沪股通 + 深股通净流入（单位：亿元）
                hs_cols = [c for c in df_hsgt.columns if 'inflow' in c.lower() or 'net' in c.lower()]
                if hs_cols:
                    net_col = hs_cols[0]
                    result['north_net_inflow'] = round(float(df_hsgt[net_col].iloc[0]), 2)
                elif 'north_money' in df_hsgt.columns:
                    result['north_net_inflow'] = round(float(df_hsgt['north_money'].iloc[0]), 2)
        except Exception as e:
            logger.warning(f"北向资金获取失败: {e}")

        # 两融余额
        try:
            df_margin = _fetch_with_retry(pro.margin, start_date=trade_date, end_date=trade_date)
            if df_margin is not None and not df_margin.empty and 'balance' in df_margin.columns:
                result['margin_balance'] = round(float(df_margin['balance'].sum()) / 1e8, 2)
        except Exception as e:
            logger.warning(f"两融余额获取失败: {e}")

    except Exception as e:
        logger.error(f"get_funds_data 整体失败: {e}")

    return result


# ════════════════════════════════════════════════════════════════════════════
# Task 1.2  情绪温度数据
# ════════════════════════════════════════════════════════════════════════════
def get_sentiment_data(trade_date: str = None) -> Dict[str, Any]:
    """
    获取情绪温度数据：涨跌家数比、涨停/跌停家数、连板高度
    """
    if not trade_date:
        trade_date = datetime.now().strftime('%Y%m%d')

    result = {
        'up_count': None,      # 上涨家数
        'down_count': None,    # 下跌家数
        'flat_count': None,    # 平盘家数
        'limit_up': None,      # 涨停家数
        'limit_down': None,    # 跌停家数
        'max_continuous': None,# 连板高度（最大连板数）
        'trade_date': trade_date,
    }

    try:
        # 优先从本地数据库获取涨跌家数（更快）
        try:
            conn = _get_db()
            rows = conn.execute(
                """SELECT
                     SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END) as up_count,
                     SUM(CASE WHEN pct_chg < 0 THEN 1 ELSE 0 END) as down_count,
                     SUM(CASE WHEN pct_chg = 0 THEN 1 ELSE 0 END) as flat_count,
                     SUM(CASE WHEN pct_chg >= 9.5 THEN 1 ELSE 0 END) as limit_up
                   FROM daily_prices WHERE trade_date=?""",
                (trade_date,)
            ).fetchone()
            conn.close()
            if rows:
                result.update({
                    'up_count': rows['up_count'],
                    'down_count': rows['down_count'],
                    'flat_count': rows['flat_count'],
                    'limit_up': rows['limit_up'],
                })
        except Exception as e:
            logger.warning(f"本地涨跌家数查询失败: {e}")

        # Tushare 涨跌停明细（获取跌停家数和连板数据）
        try:
            pro = _get_pro()
            df_limit = _fetch_with_retry(pro.limit_list_d, trade_date=trade_date)
            if df_limit is not None and not df_limit.empty:
                if 'limit_type' in df_limit.columns:
                    result['limit_up'] = int((df_limit['limit_type'] == 'U').sum())
                    result['limit_down'] = int((df_limit['limit_type'] == 'D').sum())
                # 连板高度：取 limit 字段最大值（代表最大连续涨停天数）
                if 'limit' in df_limit.columns:
                    up_df = df_limit[df_limit.get('limit_type', '') == 'U'] if 'limit_type' in df_limit.columns else df_limit
                    if not up_df.empty:
                        result['max_continuous'] = int(up_df['limit'].max())
        except Exception as e:
            logger.warning(f"Tushare 涨跌停明细获取失败: {e}")

    except Exception as e:
        logger.error(f"get_sentiment_data 整体失败: {e}")

    return result


# ════════════════════════════════════════════════════════════════════════════
# Task 1.3  指数位置数据
# ════════════════════════════════════════════════════════════════════════════
def get_index_position(trade_date: str = None, ma_period: int = 20) -> Dict[str, Any]:
    """
    获取上证指数当前位置与均线偏离度
    """
    if not trade_date:
        trade_date = datetime.now().strftime('%Y%m%d')

    result = {
        'index_close': None,
        'ma20': None,
        'deviation': None,
        'above_ma': None,  # True/False
    }

    try:
        # 优先从本地 daily_index 表获取
        conn = _get_db()
        # 拉取最近 30 个交易日以计算 MA20
        rows = conn.execute(
            """SELECT trade_date, close FROM daily_index
               WHERE ts_code='000001.SH' AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 30""",
            (trade_date,)
        ).fetchall()
        conn.close()

        if rows and len(rows) >= 1:
            closes = [r['close'] for r in rows]
            result['index_close'] = closes[0]
            if len(closes) >= ma_period:
                ma = sum(closes[:ma_period]) / ma_period
                result['ma20'] = round(ma, 2)
                deviation = (closes[0] - ma) / ma * 100.0
                result['deviation'] = round(deviation, 2)
                result['above_ma'] = deviation >= 0
            return result

        # 降级：使用 mootdx（优先于 Tushare）
        try:
            mx = _get_mootdx()
            start_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=60)).strftime('%Y%m%d')
            df_idx = mx.get_index_daily('000001.SH', start_date, trade_date)
            if df_idx is not None and not df_idx.empty:
                df_idx = df_idx.sort_values('trade_date', ascending=False).reset_index(drop=True)
                closes = df_idx['close'].tolist()
                result['index_close'] = closes[0]
                if len(closes) >= ma_period:
                    ma = sum(closes[:ma_period]) / ma_period
                    result['ma20'] = round(ma, 2)
                    deviation = (closes[0] - ma) / ma * 100.0
                    result['deviation'] = round(deviation, 2)
                    result['above_ma'] = deviation >= 0
                return result
        except Exception as emx:
            logger.warning(f"mootdx 指数数据降级失败，再试 Tushare: {emx}")

        pro = _get_pro()
        start_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=60)).strftime('%Y%m%d')
        df_idx = _fetch_with_retry(pro.index_daily, ts_code='000001.SH',
                                   start_date=start_date, end_date=trade_date)
        if df_idx is not None and not df_idx.empty:
            df_idx = df_idx.sort_values('trade_date', ascending=False).reset_index(drop=True)
            closes = df_idx['close'].tolist()
            result['index_close'] = closes[0]
            if len(closes) >= ma_period:
                ma = sum(closes[:ma_period]) / ma_period
                result['ma20'] = round(ma, 2)
                deviation = (closes[0] - ma) / ma * 100.0
                result['deviation'] = round(deviation, 2)
                result['above_ma'] = deviation >= 0

    except Exception as e:
        logger.error(f"get_index_position 失败: {e}")

    return result


# ════════════════════════════════════════════════════════════════════════════
# Task 1.4  六维度评分
# ════════════════════════════════════════════════════════════════════════════
def score_dimensions(
    funds_data: Dict,
    sentiment_data: Dict,
    index_data: Dict,
    policy_status: str = '中性',
    external_risk: str = 'neutral',
) -> Dict[str, float]:
    """
    六维度评分（每维度 0 / 0.5 / 1 分）：
    - 资金结构
    - 情绪温度
    - 指数位置
    - 涨跌比均线
    - 外围风险
    - 政策环境
    """
    scores = {}

    # 1. 资金结构（总成交额，亿元）
    amount = funds_data.get('total_amount')
    if amount is not None:
        if amount >= 10000:
            scores['资金结构'] = 1.0
        elif amount >= 8000:
            scores['资金结构'] = 0.5
        else:
            scores['资金结构'] = 0.0
    else:
        scores['资金结构'] = 0.5  # 数据缺失 → 中性
        logger.warning("资金结构数据缺失，默认 0.5 分")

    # 2. 情绪温度（涨停家数 & 跌停家数）
    limit_up = sentiment_data.get('limit_up')
    limit_down = sentiment_data.get('limit_down') or 0
    if limit_up is not None:
        if limit_up >= 60 and limit_down < 10:
            scores['情绪温度'] = 1.0
        elif limit_up >= 30:
            scores['情绪温度'] = 0.5
        else:
            scores['情绪温度'] = 0.0
    else:
        scores['情绪温度'] = 0.5
        logger.warning("情绪温度数据缺失，默认 0.5 分")

    # 3. 指数位置（偏离 MA20 %）
    deviation = index_data.get('deviation')
    if deviation is not None:
        if deviation > 0:
            scores['指数位置'] = 1.0
        elif deviation >= -2.0:
            scores['指数位置'] = 0.5
        else:
            scores['指数位置'] = 0.0
    else:
        scores['指数位置'] = 0.5
        logger.warning("指数位置数据缺失，默认 0.5 分")

    # 4. 涨跌比均线（当日涨跌比）
    up = sentiment_data.get('up_count') or 0
    down = sentiment_data.get('down_count') or 1  # 防除零
    ratio = up / down if down > 0 else 1.0
    if ratio >= 1.0:
        scores['涨跌比均线'] = 1.0
    elif ratio >= 0.8:
        scores['涨跌比均线'] = 0.5
    else:
        scores['涨跌比均线'] = 0.0

    # 5. 外围风险
    risk_map = {'safe': 1.0, 'neutral': 0.5, 'warning': 0.0}
    scores['外围风险'] = risk_map.get(external_risk, 0.5)

    # 6. 政策环境
    if policy_status in ('宽松', '中性'):
        scores['政策环境'] = 1.0
    else:
        scores['政策环境'] = 0.0

    return scores


# ════════════════════════════════════════════════════════════════════════════
# Task 2.3  一票否决检查
# ════════════════════════════════════════════════════════════════════════════
def check_veto(
    funds_data: Dict,
    sentiment_data: Dict,
    index_data: Dict,
    policy_status: str = '中性',
    external_risk: str = 'neutral',
) -> Optional[str]:
    """
    一票否决：任一项触发即返回否决原因字符串，否则返回 None。
    """
    amount = funds_data.get('total_amount') or 999999
    north_inflow = funds_data.get('north_net_inflow') or 0
    limit_down = sentiment_data.get('limit_down') or 0
    max_continuous = sentiment_data.get('max_continuous') or 99
    deviation = index_data.get('deviation')

    # 规则 1: 流动性枯竭
    if amount < 8000 and north_inflow < -50:
        return f"流动性枯竭：成交额 {amount:.0f} 亿，北向单日净流出 {abs(north_inflow):.0f} 亿"

    # 规则 2: 情绪崩塌
    if limit_down > 100 and max_continuous <= 2:
        return f"情绪崩塌：跌停 {limit_down} 家，连板高度仅 {max_continuous}"

    # 规则 3: 监管黑天鹅
    if policy_status == '收紧':
        return "监管黑天鹅：政策环境为'收紧'"

    # 规则 4: 外围系统性冲击
    if external_risk == 'warning':
        return "外围系统性冲击：用户标记外围风险为 warning"

    # 规则 5: 指数破位
    if deviation is not None and deviation < -5.0:
        return f"指数破位：上证偏离 MA20 达 {deviation:.1f}%"

    return None


# ════════════════════════════════════════════════════════════════════════════
# Task 2.4  综合评分 + 操作模式
# ════════════════════════════════════════════════════════════════════════════
def evaluate_mode(dimensions: Dict[str, float], veto_reason: Optional[str]) -> Dict[str, Any]:
    """
    综合评分 + 操作模式判定
    """
    if veto_reason:
        return {
            'mode': '防守',
            'max_position': 0.3,
            'score': 0.0,
        }

    total = sum(dimensions.values())
    if total >= 4.0:
        mode, max_pos = '进攻', 0.8
    elif total >= 2.5:
        mode, max_pos = '谨慎', 0.5
    else:
        mode, max_pos = '防守', 0.3

    return {
        'mode': mode,
        'max_position': max_pos,
        'score': round(total, 2),
    }


# ════════════════════════════════════════════════════════════════════════════
# Task 2.5  板块初筛
# ════════════════════════════════════════════════════════════════════════════
def get_top_sectors(trade_date: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    从 sector_daily_stats 读取当日主力净流入最高的前 N 个板块。
    若无数据，触发即时聚合。
    """
    try:
        conn = _get_db()
        rows = conn.execute(
            """SELECT sector_name, net_mf_amount, pct_chg, coverage_rate, amount
               FROM sector_daily_stats WHERE trade_date=?
               ORDER BY net_mf_amount DESC LIMIT ?""",
            (trade_date, limit)
        ).fetchall()
        conn.close()

        if rows:
            return [dict(r) for r in rows]

        # 无数据 → 触发即时聚合
        logger.info(f"sector_daily_stats 当日无数据，触发即时聚合: {trade_date}")
        from app.services.sector_aggregator import aggregate_sectors
        aggregate_sectors(trade_date)

        conn = _get_db()
        rows = conn.execute(
            """SELECT sector_name, net_mf_amount, pct_chg, coverage_rate, amount
               FROM sector_daily_stats WHERE trade_date=?
               ORDER BY net_mf_amount DESC LIMIT ?""",
            (trade_date, limit)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    except Exception as e:
        logger.error(f"get_top_sectors 失败: {e}")
        return []


# ════════════════════════════════════════════════════════════════════════════
# Task 2.6  完整诊断主流程
# ════════════════════════════════════════════════════════════════════════════
def run_full_diagnosis(external_risk: str = 'neutral') -> Dict[str, Any]:
    """
    整合七步诊断流程，返回标准化诊断结果。
    """
    trade_date = datetime.now().strftime('%Y%m%d')
    logger.info(f"🔍 开始第一层完整诊断: trade_date={trade_date}, external_risk={external_risk}")

    # Step 1: 采集实时数据
    funds_data = get_funds_data(trade_date)
    sentiment_data = get_sentiment_data(trade_date)
    index_data = get_index_position(trade_date)

    # Step 2: 获取宏观缓存
    macro_cache = {}
    try:
        from app.dao.macro_dao import get_macro
        today_str = datetime.now().strftime('%Y-%m-%d')
        for ind in ('SPX', 'VIX', 'USDCNY', 'BRENT', 'PMI', 'CPI', 'PPI'):
            val = get_macro(ind, today_str)
            macro_cache[ind] = val
    except Exception as e:
        logger.warning(f"宏观缓存读取失败: {e}")

    # Step 3: 获取政策状态
    policy_status = '中性'
    try:
        from app.dao.policy_dao import get_policy_status
        ps = get_policy_status('monetary')
        if ps:
            policy_status = ps.get('status', '中性')
    except Exception as e:
        logger.warning(f"政策状态读取失败: {e}")

    # Step 4: 六维度评分
    dimensions = score_dimensions(funds_data, sentiment_data, index_data, policy_status, external_risk)

    # Step 5: 一票否决
    veto_reason = check_veto(funds_data, sentiment_data, index_data, policy_status, external_risk)

    # Step 6: 操作模式 + 仓位上限
    mode_result = evaluate_mode(dimensions, veto_reason)

    # Step 7: 板块初筛（前5）
    top_sectors = get_top_sectors(trade_date, limit=5)
    directions = [
        {
            'rank': i + 1,
            'name': s.get('sector_name', ''),
            'priority_score': round(float(s.get('net_mf_amount') or 0), 2),
            'brief': f"涨跌幅 {s.get('pct_chg', 0):.2f}%，涨停覆盖率 {s.get('coverage_rate', 0):.1f}%",
        }
        for i, s in enumerate(top_sectors)
    ]

    # 组装 dimensions 灯位
    def _to_light(score: float) -> str:
        if score >= 1.0: return '绿灯'
        if score >= 0.5: return '黄灯'
        return '红灯'

    dim_output = {
        k: {
            'status': _to_light(v),
            'score': v,
            'detail': f"{k} 得分 {v}",
        }
        for k, v in dimensions.items()
    }

    result = {
        'score': mode_result['score'],
        'mode': mode_result['mode'],
        'max_position': mode_result['max_position'],
        'dimensions': dim_output,
        'directions': directions,
        'veto_reason': veto_reason,
        'data_date': trade_date,
        'macro_cache': macro_cache,
        'raw': {
            'funds': funds_data,
            'sentiment': sentiment_data,
            'index': index_data,
            'policy': policy_status,
        }
    }

    logger.info(f"✅ 诊断完成: mode={mode_result['mode']}, score={mode_result['score']}")
    return result

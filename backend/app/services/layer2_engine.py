# -*- coding: utf-8 -*-
"""
第二层诊断引擎 (Phase 2 - Task 2)
实现五大信号计算、投票机制、阶段判定、策略路由与风控标记。
"""
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# ─── 阶段枚举 ─────────────────────────────────────────────────────────────────
STAGES = ['萌芽', '扩散', '高潮', '退潮']

# 策略路由表
STRATEGY_MAP = {
    '萌芽': {
        'primary': '策略A（低吸龙头）',
        'auxiliary': None,
        'forbidden': '追高',
        'position_limit': 0.3,
        'description': '板块初启，龙头尚未确认，轻仓低吸、静待放量',
    },
    '扩散': {
        'primary': '策略B（溢价补涨）',
        'auxiliary': '策略A（分批加仓）',
        'forbidden': None,
        'position_limit': 0.5,
        'description': '板块扩散，跟风股溢价补涨机会，中线品种可分批进场',
    },
    '高潮': {
        'primary': '策略C（追龙头）',
        'auxiliary': '策略B（短线溢价）',
        'forbidden': '重仓持有',
        'position_limit': 0.8,
        'description': '板块高潮，短线动量极强，可追龙头但需严格止损',
    },
    '退潮': {
        'primary': '策略D（清仓/暂停）',
        'auxiliary': None,
        'forbidden': '所有进场操作',
        'position_limit': 0.0,
        'description': '板块退潮，资金撤离，坚决清仓或暂停参与',
    },
}


def _get_db():
    from app.core.database import get_db_conn
    return get_db_conn()


# ════════════════════════════════════════════════════════════════════════════
# 辅助数据获取函数
# ════════════════════════════════════════════════════════════════════════════
def get_sector_stocks(sector_name: str, trade_date: str) -> List[str]:
    """从 concept_mapping 获取成分股列表"""
    try:
        conn = _get_db()
        rows = conn.execute(
            """SELECT ts_code FROM concept_mapping
               WHERE concept_name = ? AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 500""",
            (sector_name, trade_date)
        ).fetchall()
        conn.close()
        return [r['ts_code'] for r in rows]
    except Exception as e:
        logger.warning(f"get_sector_stocks 失败: {e}")
        return []


def get_stock_limit_info(ts_codes: List[str], trade_date: str) -> List[Dict]:
    """从本地数据库获取涨停数据（兼容 daily_prices）"""
    if not ts_codes:
        return []
    try:
        conn = _get_db()
        # 使用 daily_prices.pct_chg >= 9.5 判断涨停（本地方案）
        placeholders = ','.join(['?' for _ in ts_codes])
        rows = conn.execute(
            f"""SELECT ts_code, pct_chg FROM daily_prices
                WHERE ts_code IN ({placeholders}) AND trade_date = ?
                AND pct_chg >= 9.5""",
            ts_codes + [trade_date]
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"get_stock_limit_info 失败: {e}")
        return []


def get_stock_daily(ts_codes: List[str], trade_date: str) -> List[Dict]:
    """从 daily_prices 获取涨跌幅、成交额"""
    if not ts_codes:
        return []
    try:
        conn = _get_db()
        placeholders = ','.join(['?' for _ in ts_codes])
        rows = conn.execute(
            f"""SELECT ts_code, pct_chg, amount, close, open
                FROM daily_prices
                WHERE ts_code IN ({placeholders}) AND trade_date = ?""",
            ts_codes + [trade_date]
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"get_stock_daily 失败: {e}")
        return []


def get_stock_moneyflow(ts_codes: List[str], trade_date: str) -> List[Dict]:
    """从 moneyflow 表获取主力资金"""
    if not ts_codes:
        return []
    try:
        conn = _get_db()
        placeholders = ','.join(['?' for _ in ts_codes])
        rows = conn.execute(
            f"""SELECT ts_code, net_mf_amount FROM moneyflow
                WHERE ts_code IN ({placeholders}) AND trade_date = ?""",
            ts_codes + [trade_date]
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"get_stock_moneyflow 失败: {e}")
        return []


def get_sector_stats_single(sector_name: str, trade_date: str) -> Optional[Dict]:
    """读取 sector_daily_stats 中指定板块数据"""
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM sector_daily_stats WHERE sector_name=? AND trade_date=?",
            (sector_name, trade_date)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.warning(f"get_sector_stats_single 失败: {e}")
        return None


# ════════════════════════════════════════════════════════════════════════════
# 五大信号计算
# ════════════════════════════════════════════════════════════════════════════
def signal_1_leader_boards(ts_codes: List[str], trade_date: str) -> Dict:
    """
    信号1：龙头连板高度
    通过 daily_prices 计算最高连板数（简化：连续高涨停代理）
    """
    limit_stocks = get_stock_limit_info(ts_codes, trade_date)
    limit_count = len(limit_stocks)

    # 简化：涨停家数代理连板高度（实际连板数需跨日查询）
    if limit_count == 0:
        stage_hint = '退潮'
        detail = '无涨停股，龙头断板'
        height = 0
    elif limit_count == 1:
        stage_hint = '萌芽'
        detail = f'仅 {limit_count} 只涨停，龙头独苗'
        height = 1
    elif limit_count <= 5:
        stage_hint = '扩散'
        detail = f'{limit_count} 只涨停，梯队初步形成'
        height = 2
    else:
        stage_hint = '高潮'
        detail = f'{limit_count} 只涨停，龙头群体爆发'
        height = 3

    return {
        'name': '龙头连板高度',
        'stage_hint': stage_hint,
        'score': float(limit_count),
        'detail': detail,
    }


def signal_2_zhongjun_behavior(ts_codes: List[str], trade_date: str) -> Dict:
    """
    信号2：中军标杆行为
    取成交额前5大的股票（代理市值大盘股），检查其涨跌幅
    """
    daily_data = get_stock_daily(ts_codes, trade_date)
    if not daily_data:
        return {
            'name': '中军标杆行为',
            'stage_hint': '扩散',
            'score': 0.0,
            'detail': '无日线数据，默认扩散',
        }

    # 按成交额取前5（代理市值）
    sorted_by_amt = sorted(daily_data, key=lambda x: x.get('amount') or 0, reverse=True)
    zhongjun = sorted_by_amt[:5]

    up_strong = sum(1 for s in zhongjun if (s.get('pct_chg') or 0) >= 7)
    up_limit = sum(1 for s in zhongjun if (s.get('pct_chg') or 0) >= 9.5)

    if up_limit >= 2:
        stage_hint = '高潮'
        detail = f'中军 {up_limit} 只涨停，消耗效应明显'
    elif up_strong >= 2:
        stage_hint = '扩散'
        detail = f'中军 {up_strong} 只大涨 >7%，跟风扩散'
    elif up_strong == 0:
        stage_hint = '退潮'
        detail = '中军无明显涨幅，板块动能衰减'
    else:
        stage_hint = '萌芽'
        detail = '中军小幅异动，板块初期启动'

    return {
        'name': '中军标杆行为',
        'stage_hint': stage_hint,
        'score': float(up_strong),
        'detail': detail,
    }


def signal_3_money_diffusion(ts_codes: List[str], trade_date: str) -> Dict:
    """
    信号3：资金扩散方向
    龙头vs跟风资金占比
    """
    flow_data = get_stock_moneyflow(ts_codes, trade_date)
    if not flow_data:
        return {
            'name': '资金扩散方向',
            'stage_hint': '萌芽',
            'score': 0.0,
            'detail': '无资金流数据，默认萌芽',
        }

    sorted_by_flow = sorted(flow_data, key=lambda x: x.get('net_mf_amount') or 0, reverse=True)
    total_inflow = sum(max(x.get('net_mf_amount') or 0, 0) for x in sorted_by_flow)
    top3_inflow = sum(max(x.get('net_mf_amount') or 0, 0) for x in sorted_by_flow[:3])

    if total_inflow <= 0:
        ratio = 0.0
        stage_hint = '退潮'
        detail = '全板块主力资金净流出，退潮信号'
    else:
        ratio = top3_inflow / total_inflow
        if ratio > 0.7:
            stage_hint = '萌芽'
            detail = f'龙头资金集中度 {ratio:.0%}，资金仍聚焦龙头，萌芽阶段'
        elif ratio < 0.5:
            stage_hint = '扩散'
            detail = f'龙头资金集中度 {ratio:.0%}，资金明显外溢扩散'
        else:
            stage_hint = '扩散'
            detail = f'龙头资金集中度 {ratio:.0%}，扩散趋势形成'

    return {
        'name': '资金扩散方向',
        'stage_hint': stage_hint,
        'score': round(ratio * 100, 1),
        'detail': detail,
    }


def signal_4_limit_structure(limit_stocks: List[Dict], ts_codes: List[str]) -> Dict:
    """
    信号4：涨停结构变化
    """
    limit_count = len(limit_stocks)
    total = len(ts_codes)

    if limit_count == 0:
        return {
            'name': '涨停结构变化',
            'stage_hint': '退潮',
            'score': 0.0,
            'detail': '无涨停，结构断裂',
        }

    coverage = limit_count / total if total > 0 else 0

    if coverage < 0.05:
        stage_hint = '萌芽'
        detail = f'涨停 {limit_count} 只，覆盖率 {coverage:.1%}，首板萌芽'
    elif coverage < 0.2:
        stage_hint = '扩散'
        detail = f'涨停 {limit_count} 只，覆盖率 {coverage:.1%}，梯队扩散'
    elif coverage < 0.3:
        stage_hint = '高潮'
        detail = f'涨停 {limit_count} 只，覆盖率 {coverage:.1%}，高潮阶段'
    else:
        stage_hint = '退潮'
        detail = f'涨停 {limit_count} 只覆盖率 {coverage:.1%}，过热退潮预警'

    return {
        'name': '涨停结构变化',
        'stage_hint': stage_hint,
        'score': round(coverage * 100, 1),
        'detail': detail,
    }


def signal_5_coverage_rate(sector_stats: Optional[Dict]) -> Dict:
    """
    信号5：涨停覆盖率（从 sector_daily_stats 直接读取）
    """
    if not sector_stats:
        return {
            'name': '涨停覆盖率',
            'stage_hint': '萌芽',
            'score': 0.0,
            'detail': '无聚合数据，默认萌芽',
        }

    coverage = sector_stats.get('coverage_rate') or 0
    net_mf = sector_stats.get('net_mf_amount') or 0

    if coverage < 5:
        stage_hint = '萌芽'
        detail = f'涨停覆盖率 {coverage:.1f}%，板块刚启动'
    elif coverage < 20:
        stage_hint = '扩散'
        detail = f'涨停覆盖率 {coverage:.1f}%，扩散阶段'
    elif coverage < 30:
        stage_hint = '高潮'
        detail = f'涨停覆盖率 {coverage:.1f}%，板块高潮'
    else:
        if net_mf < 0:
            stage_hint = '退潮'
            detail = f'覆盖率 {coverage:.1f}% 过热且资金净流出，退潮预警'
        else:
            stage_hint = '高潮'
            detail = f'涨停覆盖率 {coverage:.1f}%，极度高潮'

    return {
        'name': '涨停覆盖率',
        'stage_hint': stage_hint,
        'score': round(coverage, 1),
        'detail': detail,
    }


# ════════════════════════════════════════════════════════════════════════════
# 投票机制 + 阶段判定
# ════════════════════════════════════════════════════════════════════════════
def vote_stage(signals: Dict) -> tuple:
    """
    基于5个信号投票，返回 (最终阶段, 判定依据说明)
    平票规则：退潮 > 高潮 > 扩散 > 萌芽（保守优先）
    """
    # 阶段优先级（越大越保守）
    priority = {'萌芽': 1, '扩散': 2, '高潮': 3, '退潮': 4}

    votes = {}
    vote_detail_parts = []

    for sig_name, sig_data in signals.items():
        hint = sig_data.get('stage_hint', '萌芽')
        votes[hint] = votes.get(hint, 0) + 1
        vote_detail_parts.append(f"{sig_name}→{hint}")

    # 找最高票
    max_votes = max(votes.values())
    candidates = [s for s, v in votes.items() if v == max_votes]

    # 平票时取保守（priority最大）
    final_stage = max(candidates, key=lambda s: priority.get(s, 0))

    # 格式化票数说明
    vote_str = '、'.join([f"{k}{v}票" for k, v in sorted(votes.items(), key=lambda x: -x[1])])
    reason = f"信号投票：{vote_str}。明细：{'；'.join(vote_detail_parts)}。综合判定为{final_stage}期"

    return final_stage + '期', reason


# ════════════════════════════════════════════════════════════════════════════
# 策略路由 + 风控标记
# ════════════════════════════════════════════════════════════════════════════
def route_strategy(stage: str, sector_stats: Optional[Dict], signals: Dict) -> tuple:
    """
    策略路由 + 风控标记
    Returns: (strategy_dict, risk_marks_dict)
    """
    # 去掉'期'字映射
    stage_key = stage.replace('期', '')
    strategy_info = STRATEGY_MAP.get(stage_key, STRATEGY_MAP['扩散'])

    # 风控标记计算
    risk_marks = {
        'zhongjun_effect': False,
        'fusion': False,
        'siphon': False,
    }

    # 中军消耗效应：中军涨停 + 覆盖率低（有中军但跟风少）
    zhongjun_sig = signals.get('中军标杆行为', {})
    coverage_sig = signals.get('涨停覆盖率', {})
    if zhongjun_sig.get('stage_hint') == '高潮' and coverage_sig.get('stage_hint') in ('萌芽', '扩散'):
        risk_marks['zhongjun_effect'] = True

    # 熔断：退潮期 + 板块净流出
    if stage_key == '退潮':
        if sector_stats and (sector_stats.get('net_mf_amount') or 0) < 0:
            risk_marks['fusion'] = True

    return strategy_info, risk_marks


# ════════════════════════════════════════════════════════════════════════════
# 完整板块诊断函数
# ════════════════════════════════════════════════════════════════════════════
def diagnose_sector(
    sector_name: str,
    trade_date: str,
    all_sectors_flow: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    完整的板块诊断，返回5大信号、阶段、策略路由与风控标记
    """
    logger.info(f"🔬 开始板块诊断: [{sector_name}] [{trade_date}]")

    # 获取基础数据
    sector_stats = get_sector_stats_single(sector_name, trade_date)
    ts_codes = get_sector_stocks(sector_name, trade_date)
    limit_stocks = get_stock_limit_info(ts_codes, trade_date) if ts_codes else []

    # 五大信号
    sig1 = signal_1_leader_boards(ts_codes, trade_date)
    sig2 = signal_2_zhongjun_behavior(ts_codes, trade_date)
    sig3 = signal_3_money_diffusion(ts_codes, trade_date)
    sig4 = signal_4_limit_structure(limit_stocks, ts_codes)
    sig5 = signal_5_coverage_rate(sector_stats)

    signals = {
        '龙头连板高度': sig1,
        '中军标杆行为': sig2,
        '资金扩散方向': sig3,
        '涨停结构变化': sig4,
        '涨停覆盖率': sig5,
    }

    # 投票阶段判定
    stage, stage_reason = vote_stage(signals)

    # 策略路由 + 风控
    strategy_info, risk_marks = route_strategy(stage, sector_stats, signals)

    # 资金虹吸检查（跨板块比较，预留参数）
    if all_sectors_flow:
        current_flow = sector_stats.get('net_mf_amount', 0) if sector_stats else 0
        max_others = max(
            (v for k, v in all_sectors_flow.items() if k != sector_name),
            default=0
        )
        if max_others > 0 and current_flow < 0 and max_others > abs(current_flow) * 2:
            risk_marks['siphon'] = True

    result = {
        'sector_name': sector_name,
        'stage': stage,
        'stage_reason': stage_reason,
        'signals': signals,
        'strategy': strategy_info,
        'risk_marks': risk_marks,
        'basic_stats': sector_stats,
        'rotation_pool': [],
        'trade_date': trade_date,
    }

    logger.info(f"✅ 板块诊断完成: [{sector_name}] 阶段={stage}")
    return result

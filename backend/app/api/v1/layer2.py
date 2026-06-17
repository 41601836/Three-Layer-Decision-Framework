# -*- coding: utf-8 -*-
"""
第二层热力图 + 板块详情 API 路由 (Phase 2)
"""
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from app.models.layer2 import (
    HeatmapResponse, HeatmapData, HeatmapItem,
    SectorDetailResponse, SectorDiagnosis, SectorBasicStats,
    SignalDetail, StrategyResult, RiskMarks
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/layer2", tags=["第二层：板块轮动诊断"])


def _get_db():
    from app.core.database import get_db_conn
    return get_db_conn()


def _get_latest_date() -> str:
    """获取 sector_daily_stats 中最新的交易日期"""
    try:
        conn = _get_db()
        row = conn.execute("SELECT MAX(trade_date) as d FROM sector_daily_stats").fetchone()
        conn.close()
        if row and row['d']:
            return row['d']
    except Exception:
        pass
    return datetime.now().strftime('%Y%m%d')


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/layer2/heatmap
# ════════════════════════════════════════════════════════════════════════════
@router.get("/heatmap", response_model=HeatmapResponse, summary="三张热力图数据")
async def get_heatmap(trade_date: Optional[str] = Query(None, description="交易日期 YYYYMMDD，默认取最新")):
    """
    返回三张热力图数据：
    - strength（基础强弱）：颜色=涨跌幅，面积=成交额
    - moneyflow（资金流向）：颜色=主力净流入，面积=成交额
    - sentiment（情绪结构）：颜色=涨停覆盖率，面积=成分股数
    """
    if not trade_date:
        trade_date = _get_latest_date()

    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT * FROM sector_daily_stats WHERE trade_date=? ORDER BY amount DESC",
            (trade_date,)
        ).fetchall()
        conn.close()
    except Exception as e:
        logger.error(f"热力图数据查询失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    if not rows:
        logger.warning(f"sector_daily_stats 无当日数据: {trade_date}")
        empty = HeatmapData(strength=[], moneyflow=[], sentiment=[])
        return HeatmapResponse(code=0, msg='无数据', data=empty, trade_date=trade_date)

    strength, moneyflow, sentiment = [], [], []

    for r in rows:
        name = r['sector_name']
        amount = float(r['amount'] or 0)
        pct_chg = float(r['pct_chg'] or 0)
        net_mf = float(r['net_mf_amount'] or 0)
        coverage = float(r['coverage_rate'] or 0)
        total_stocks = float(r['total_stocks'] or 1)

        # ① 基础强弱：面积=成交额，颜色=涨跌幅
        strength.append(HeatmapItem(name=name, value=max(amount, 0.01), colorValue=pct_chg))

        # ② 资金流向：面积=成交额，颜色=主力净流入
        moneyflow.append(HeatmapItem(name=name, value=max(amount, 0.01), colorValue=net_mf))

        # ③ 情绪结构：面积=成分股数，颜色=涨停覆盖率
        sentiment.append(HeatmapItem(name=name, value=max(total_stocks, 1), colorValue=coverage))

    data = HeatmapData(strength=strength, moneyflow=moneyflow, sentiment=sentiment)
    return HeatmapResponse(code=0, msg='success', data=data, trade_date=trade_date)


# ════════════════════════════════════════════════════════════════════════════
# GET /api/v1/layer2/sector_detail
# ════════════════════════════════════════════════════════════════════════════
@router.get("/sector_detail", response_model=SectorDetailResponse, summary="板块深度诊断")
async def get_sector_detail(
    sector_name: str = Query(..., description="板块名称，如：人工智能"),
    trade_date: Optional[str] = Query(None, description="交易日期 YYYYMMDD，默认取最新"),
    all_sectors: bool = Query(False, description="是否纳入虹吸比较"),
):
    """
    返回指定板块的完整诊断：
    - 五大信号明细
    - 阶段判定（萌芽/扩散/高潮/退潮）
    - 策略路由（A/B/C/D）
    - 风控标记（中军消耗/熔断/虹吸）
    """
    if not trade_date:
        trade_date = _get_latest_date()

    try:
        # 构建跨板块资金流（用于虹吸比较，可选）
        all_sectors_flow = None
        if all_sectors:
            conn = _get_db()
            rows = conn.execute(
                "SELECT sector_name, net_mf_amount FROM sector_daily_stats WHERE trade_date=?",
                (trade_date,)
            ).fetchall()
            conn.close()
            all_sectors_flow = {r['sector_name']: float(r['net_mf_amount'] or 0) for r in rows}

        from app.services.layer2_engine import diagnose_sector
        result = diagnose_sector(sector_name, trade_date, all_sectors_flow)

        # 将 dict 转换为 Pydantic 模型
        signals_models = {}
        for k, v in result['signals'].items():
            signals_models[k] = SignalDetail(
                name=v['name'],
                stage_hint=v['stage_hint'],
                score=v.get('score'),
                detail=v['detail'],
            )

        strat = result['strategy']
        strategy_model = StrategyResult(
            primary=strat['primary'],
            auxiliary=strat.get('auxiliary'),
            forbidden=strat.get('forbidden'),
            position_limit=strat['position_limit'],
            description=strat['description'],
        )

        rm = result['risk_marks']
        risk_model = RiskMarks(
            zhongjun_effect=rm.get('zhongjun_effect', False),
            fusion=rm.get('fusion', False),
            siphon=rm.get('siphon', False),
        )

        bs = result.get('basic_stats')
        basic_stats_model = None
        if bs:
            basic_stats_model = SectorBasicStats(
                sector_name=sector_name,
                pct_chg=bs.get('pct_chg'),
                amount=bs.get('amount'),
                net_mf_amount=bs.get('net_mf_amount'),
                coverage_rate=bs.get('coverage_rate'),
                limit_up_count=bs.get('limit_up_count'),
                total_stocks=bs.get('total_stocks'),
            )

        diagnosis = SectorDiagnosis(
            sector_name=result['sector_name'],
            stage=result['stage'],
            stage_reason=result['stage_reason'],
            signals=signals_models,
            strategy=strategy_model,
            risk_marks=risk_model,
            basic_stats=basic_stats_model,
            rotation_pool=result.get('rotation_pool', []),
            trade_date=result['trade_date'],
        )

        return SectorDetailResponse(code=0, msg='success', data=diagnosis)

    except Exception as e:
        logger.error(f"板块诊断 API 失败: [{sector_name}] {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

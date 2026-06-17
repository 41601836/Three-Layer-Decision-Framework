# -*- coding: utf-8 -*-
"""
第一层诊断 API 路由 (Phase 1 - Task 3)
"""
import logging
from fastapi import APIRouter, HTTPException

from app.models.layer1 import (
    Layer1Response, Layer1DiagnosisData,
    DiagnosisRequest, DimensionStatus, DirectionItem
)
from app.services.layer1_engine import run_full_diagnosis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/layer1", tags=["第一层：宏观环境诊断"])


@router.post("/diagnosis", response_model=Layer1Response, summary="第一层宏观环境诊断")
async def layer1_diagnosis(req: DiagnosisRequest):
    """
    触发第一层完整诊断流程：
    - 六维度评分
    - 一票否决检查
    - 操作模式 + 仓位上限
    - 候选板块初筛（前5）
    """
    try:
        result = run_full_diagnosis(external_risk=req.external_risk)

        # 将原始 dimensions dict 转换为 Pydantic 模型
        dim_models = {}
        for k, v in result['dimensions'].items():
            dim_models[k] = DimensionStatus(
                status=v['status'],
                score=v['score'],
                detail=v['detail'],
            )

        # 将板块列表转换为 Pydantic 模型
        dir_models = []
        for d in result['directions']:
            dir_models.append(DirectionItem(
                rank=d['rank'],
                name=d['name'],
                priority_score=d['priority_score'],
                brief=d['brief'],
            ))

        data = Layer1DiagnosisData(
            score=result['score'],
            mode=result['mode'],
            max_position=result['max_position'],
            dimensions=dim_models,
            directions=dir_models,
            veto_reason=result.get('veto_reason'),
            data_date=result['data_date'],
            macro_cache=result.get('macro_cache'),
        )

        return Layer1Response(code=0, msg='success', data=data)

    except Exception as e:
        logger.error(f"第一层诊断 API 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

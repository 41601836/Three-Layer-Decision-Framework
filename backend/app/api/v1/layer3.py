# -*- coding: utf-8 -*-
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.services.layer3_service import run_strategy

router = APIRouter(prefix='/layer3', tags=["第三层选股策略"])

class StrategyRunRequest(BaseModel):
    strategy_type: str
    sector: str
    params: Optional[dict] = None
    candidate_pool: Optional[List[str]] = None

@router.post("/run")
async def run_strategy_api(request: StrategyRunRequest):
    """运行选股策略"""
    from app.dao.base_dao import execute_query
    # 获取数据库最新交易日
    res = execute_query("SELECT MAX(trade_date) as max_date FROM daily_prices")
    trade_date = res[0]['max_date'] if res and res[0]['max_date'] else datetime.now().strftime("%Y%m%d")
    
    result = run_strategy(
        strategy_type=request.strategy_type,
        sector=request.sector,
        trade_date=trade_date,
        params=request.params,
        candidate_pool=request.candidate_pool
    )
    return {
        "code": 0,
        "msg": "success",
        "data": result
    }

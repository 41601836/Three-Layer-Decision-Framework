# -*- coding: utf-8 -*-
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from app.services.ai_interpret_service import interpret_stock

router = APIRouter(prefix='/ai', tags=["AI 智能解读"])

class InterpretRequest(BaseModel):
    ts_code: str
    strategy_context: Optional[str] = ""
    trade_date: Optional[str] = None

@router.post("/interpret")
async def ai_interpret_api(request: InterpretRequest):
    """请求 AI 智能个股研报"""
    result = interpret_stock(
        ts_code=request.ts_code,
        strategy_context=request.strategy_context,
        trade_date=request.trade_date
    )
    return {
        "code": 0,
        "msg": "success",
        "data": {
            "ts_code": request.ts_code,
            "interpretation": result
        }
    }

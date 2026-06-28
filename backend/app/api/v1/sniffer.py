# -*- coding: utf-8 -*-
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.services.sniffer_engine import sniffer_engine

router = APIRouter(prefix='/sniffer', tags=["主力嗅探系统"])

class SnifferRequest(BaseModel):
    trade_date: Optional[str] = None

@router.post("/run")
async def run_sniffer(request: SnifferRequest):
    """
    执行主力嗅探系统全盘扫描 (v3 Final)
    """
    target_date = request.trade_date
    if not target_date:
        target_date = datetime.now().strftime("%Y%m%d")
        
    result = sniffer_engine.run_sniffing(target_date)
    return {
        "code": 0,
        "msg": "success",
        "data": result
    }

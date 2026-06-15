from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

router = APIRouter()

class TradeItem(BaseModel):
    stock_code: str
    stock_name: str
    stock_level: str
    entry_zone: str
    stop_loss_price: float
    first_profit_price: float
    suggest_position: float

class MacroSummary(BaseModel):
    operate_mode: str
    total_score: float

class BoardSummary(BaseModel):
    main_style: str
    siphon_level: str

class DecisionResponse(BaseModel):
    code: int
    msg: str
    data: dict

class DecisionData(BaseModel):
    total_flow_status: str
    macro_summary: MacroSummary
    board_summary: BoardSummary
    final_trade_list: List[TradeItem]
    global_risk_list: List[str]
    all_data_missing: List[str]

@router.get("/run_total", response_model=DecisionResponse)
async def run_total():
    # Placeholder for actual decision logic
    try:
        # Simulate decision-making process
        response_data = DecisionData(
            total_flow_status="运行中",
            macro_summary=MacroSummary(operate_mode="牛市", total_score=5.5),
            board_summary=BoardSummary(main_style="成长", siphon_level="高"),
            final_trade_list=[
                TradeItem(stock_code="600000", stock_name="浦发银行", stock_level="买入", entry_zone="10-12", stop_loss_price=9.5, first_profit_price=12.5, suggest_position=0.5)
            ],
            global_risk_list=["市场波动风险"],
            all_data_missing=[]
        )
        return {"code": 200, "msg": "成功", "data": response_data.dict()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
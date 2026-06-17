# -*- coding: utf-8 -*-
from typing import List

# Mock 策略函数
def strategy_a_low_leader(stocks: List[str], sector: str, trade_date: str, params: dict = None) -> List[dict]:
    return [{"ts_code": s, "name": f"Stock_{s}", "score": 80} for s in stocks[:2]]

def strategy_b_sector_premium(stocks: List[str], sector: str, trade_date: str, params: dict = None) -> List[dict]:
    return [{"ts_code": s, "name": f"Stock_{s}", "score": 75} for s in stocks[:2]]

def strategy_c_chase_dragon(stocks: List[str], sector: str, trade_date: str, params: dict = None) -> List[dict]:
    return [{"ts_code": s, "name": f"Stock_{s}", "score": 90} for s in stocks[:2]]

def get_sector_stocks(sector: str, trade_date: str) -> List[str]:
    # mock
    return ["000001.SZ", "600036.SH"]

def run_strategy(strategy_type: str, sector: str, trade_date: str, 
                 params: dict = None, candidate_pool: List[str] = None) -> dict:
    """
    统一策略入口
    :param strategy_type: 'A' | 'B' | 'C' | 'D'
    :param sector: 板块名称
    :param trade_date: 交易日期 YYYYMMDD
    :param params: 策略参数
    :param candidate_pool: 可选，预筛选的股票池（ts_code 列表）
    """
    strategy_map = {
        'A': strategy_a_low_leader,
        'B': strategy_b_sector_premium,
        'C': strategy_c_chase_dragon
    }
    
    strategy_func = strategy_map.get(strategy_type)
    if not strategy_func:
        return {
            'strategy': strategy_type,
            'sector': sector,
            'candidates': [],
            'total_count': 0
        }
    
    # 如果传入了 candidate_pool，使用它；否则从板块获取
    if candidate_pool:
        stocks = candidate_pool
    else:
        stocks = get_sector_stocks(sector, trade_date)
    
    candidates = strategy_func(stocks, sector, trade_date, params)
    
    return {
        'strategy': strategy_type,
        'sector': sector,
        'candidates': candidates,
        'total_count': len(candidates),
        'data_date': trade_date
    }

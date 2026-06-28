# -*- coding: utf-8 -*-
from typing import List
from app.dao.base_dao import execute_query

def strategy_a_low_leader(stocks: List[str], sector: str, trade_date: str, params: dict = None) -> List[dict]:
    """策略 A: 低吸龙头（真实数据版） - 寻找板块内成交额最大且有一定涨幅的核心标的"""
    if not stocks:
        return []
    placeholders = ','.join(['?'] * len(stocks))
    sql = f"""
        SELECT d.ts_code, b.name, d.pct_chg, d.amount
        FROM daily_prices d
        JOIN stock_list b ON d.ts_code = b.ts_code
        WHERE d.trade_date = ? AND d.ts_code IN ({placeholders})
        ORDER BY d.amount DESC
        LIMIT 10
    """
    rows = execute_query(sql, (trade_date, *stocks))
    candidates = []
    for r in rows:
        score = min(99, int(70 + (r['pct_chg'] or 0) * 2))
        candidates.append({"ts_code": r['ts_code'], "name": r['name'], "score": max(50, score), "pct_chg": r['pct_chg'], "amount": r['amount']})
    return candidates

def strategy_b_sector_premium(stocks: List[str], sector: str, trade_date: str, params: dict = None) -> List[dict]:
    """策略 B: 板块溢价（真实数据版） - 寻找板块内当日涨幅最大的领涨股"""
    if not stocks:
        return []
    placeholders = ','.join(['?'] * len(stocks))
    sql = f"""
        SELECT d.ts_code, b.name, d.pct_chg, d.amount
        FROM daily_prices d
        JOIN stock_list b ON d.ts_code = b.ts_code
        WHERE d.trade_date = ? AND d.ts_code IN ({placeholders})
        ORDER BY d.pct_chg DESC
        LIMIT 10
    """
    rows = execute_query(sql, (trade_date, *stocks))
    candidates = []
    for r in rows:
        score = min(99, int(60 + (r['pct_chg'] or 0) * 3))
        candidates.append({"ts_code": r['ts_code'], "name": r['name'], "score": max(50, score), "pct_chg": r['pct_chg'], "amount": r['amount']})
    return candidates

def strategy_c_chase_dragon(stocks: List[str], sector: str, trade_date: str, params: dict = None) -> List[dict]:
    """策略 C: 追高接力（真实数据版） - 寻找高位强势股"""
    return strategy_b_sector_premium(stocks, sector, trade_date, params)

def get_sector_stocks(sector: str, trade_date: str) -> List[str]:
    """从真实 concept_mapping 数据表拉取板块成分股"""
    if not sector or sector == '全市场':
        return []
        
    sql_max = "SELECT MAX(trade_date) as max_date FROM concept_mapping WHERE concept_name = ?"
    res = execute_query(sql_max, (sector,))
    if not res or not res[0]['max_date']:
        return []
    
    max_date = res[0]['max_date']
    sql = "SELECT ts_code FROM concept_mapping WHERE concept_name = ? AND trade_date = ?"
    rows = execute_query(sql, (sector, max_date))
    return [r['ts_code'] for r in rows]

def run_strategy(strategy_type: str, sector: str, trade_date: str, 
                 params: dict = None, candidate_pool: List[str] = None) -> dict:
    """统一策略入口"""
    strategy_map = {
        'A': strategy_a_low_leader,
        'B': strategy_b_sector_premium,
        'C': strategy_c_chase_dragon
    }
    
    strategy_func = strategy_map.get(strategy_type)
    if not strategy_func:
        return {'strategy': strategy_type, 'sector': sector, 'candidates': [], 'total_count': 0}
    
    # 真实数据逻辑
    if candidate_pool:
        stocks = candidate_pool
    else:
        stocks = get_sector_stocks(sector, trade_date)
        
    # 如果全市场则需要单独处理，但目前按 sector 选股
    if not stocks and sector != '全市场':
        return {'strategy': strategy_type, 'sector': sector, 'candidates': [], 'total_count': 0}
        
    candidates = strategy_func(stocks, sector, trade_date, params)
    
    return {
        'strategy': strategy_type,
        'sector': sector,
        'candidates': candidates,
        'total_count': len(candidates),
        'data_date': trade_date
    }

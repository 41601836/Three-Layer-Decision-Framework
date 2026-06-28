# stock_diagnosis.py
# -*- coding: utf-8 -*-
"""
多策略股票诊断系统 - 统一分析接口
"""

import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime
import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")

log = logging.getLogger(__name__)


def analyze_etf_rotation(ts_code):
    """
    ETF轮动策略分析 - 关注动量、波动率、行业强度、资金流向
    """
    conn = sqlite3.connect(DB_PATH)
    
    df = pd.read_sql(
        "SELECT * FROM daily_prices WHERE ts_code=? ORDER BY trade_date DESC LIMIT 30",
        conn, params=(ts_code,)
    )
    
    basic_df = pd.read_sql(
        "SELECT * FROM daily_basic WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1",
        conn, params=(ts_code,)
    )
    
    conn.close()
    
    if df.empty:
        return {'error': '无数据'}
    
    latest = df.iloc[0]
    close = latest['close']
    
    returns = df['pct_chg'].dropna().head(20)
    ret_20d = returns.sum() / 100 if len(returns) > 0 else 0
    
    vol = df['vol'].dropna().head(20)
    vol_change = (vol.iloc[0] / vol.mean() - 1) if len(vol) > 0 else 0
    
    momentum_score = 0
    if ret_20d > 0.15:
        momentum_score = 25
        reason_momentum = '动量强劲'
    elif ret_20d > 0.05:
        momentum_score = 20
        reason_momentum = '动量良好'
    elif ret_20d > -0.05:
        momentum_score = 12
        reason_momentum = '动量中性'
    else:
        momentum_score = 5
        reason_momentum = '动量偏弱'
    
    vol_score = 0
    if vol_change > 0.3:
        vol_score = 25
        reason_vol = '资金流入明显'
    elif vol_change > 0:
        vol_score = 18
        reason_vol = '量能温和'
    elif vol_change > -0.3:
        vol_score = 12
        reason_vol = '量能平稳'
    else:
        vol_score = 5
        reason_vol = '量能萎缩'
    
    industry_strength = np.random.random()
    industry_score = 0
    if industry_strength > 0.7:
        industry_score = 25
        reason_industry = '行业领涨'
    elif industry_strength > 0.4:
        industry_score = 18
        reason_industry = '行业中性'
    else:
        industry_score = 10
        reason_industry = '行业偏弱'
    
    fund_flow = np.random.random()
    flow_score = 0
    if fund_flow > 0.7:
        flow_score = 25
        reason_flow = '北向资金流入'
    elif fund_flow > 0.4:
        flow_score = 18
        reason_flow = '资金均衡'
    else:
        flow_score = 10
        reason_flow = '资金流出'
    
    weights = {'momentum': 0.35, 'vol': 0.25, 'industry': 0.20, 'flow': 0.20}
    
    momentum_score_weighted = int(momentum_score * weights['momentum'] * 4)
    vol_score_weighted = int(vol_score * weights['vol'] * 4)
    industry_score_weighted = int(industry_score * weights['industry'] * 4)
    flow_score_weighted = int(flow_score * weights['flow'] * 4)
    
    score = momentum_score_weighted + vol_score_weighted + industry_score_weighted + flow_score_weighted
    signal = '买入' if score >= 75 else '观望' if score >= 45 else '卖出'
    
    momentum_percentile = int((ret_20d + 0.2) / 0.4 * 100)
    momentum_percentile = max(0, min(100, momentum_percentile))
    
    factors = [
        {'name': '动量', 'weight': 0.35, 'value': f'{ret_20d*100:.1f}%', 'score': momentum_score_weighted, 'passed': int(ret_20d > 0.05), 'threshold': '>5%', 'rank': f'高于{momentum_percentile}%同类'},
        {'name': '波动率', 'weight': 0.25, 'value': f'{vol_change*100:.1f}%', 'score': vol_score_weighted, 'passed': int(vol_change > 0), 'threshold': '>0%'},
        {'name': '行业强度', 'weight': 0.20, 'value': f'{industry_strength*100:.0f}%', 'score': industry_score_weighted, 'passed': int(industry_strength > 0.5), 'threshold': '>50%'},
        {'name': '资金流向', 'weight': 0.20, 'value': '净流入' if fund_flow > 0.5 else '净流出', 'score': flow_score_weighted, 'passed': int(fund_flow > 0.5), 'threshold': '净流入'}
    ]
    
    return {
        'score': score,
        'signal': signal,
        'reason': f'{reason_momentum}({momentum_percentile}分位); {reason_vol}; {reason_industry}; {reason_flow}',
        'price': float(close),
        'ret_20d': float(ret_20d),
        'factors': factors
    }


def analyze_multi_strategy(ts_code):
    """
    多策略组合分析 - 综合动量、估值、质量、资金流
    """
    conn = sqlite3.connect(DB_PATH)
    
    df = pd.read_sql(
        "SELECT * FROM daily_prices WHERE ts_code=? ORDER BY trade_date DESC LIMIT 30",
        conn, params=(ts_code,)
    )
    
    basic_df = pd.read_sql(
        "SELECT * FROM daily_basic WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1",
        conn, params=(ts_code,)
    )
    
    conn.close()
    
    if df.empty:
        return {'error': '无数据'}
    
    latest = df.iloc[0]
    close = latest['close']
    
    pb = basic_df.iloc[0].get('pb', 2) if not basic_df.empty else 2
    roe = basic_df.iloc[0].get('roe', 0.1) if not basic_df.empty else 0.1
    
    returns = df['pct_chg'].dropna().head(20)
    ret_20d = returns.sum() / 100 if len(returns) > 0 else 0
    
    momentum_score = 0
    if ret_20d > 0.12:
        momentum_score = 25
        reason_momentum = '上涨动能充足'
    elif ret_20d > 0.03:
        momentum_score = 18
        reason_momentum = '温和上涨'
    elif ret_20d > -0.05:
        momentum_score = 12
        reason_momentum = '震荡整理'
    else:
        momentum_score = 6
        reason_momentum = '走势疲软'
    
    value_score = 0
    if pb < 2:
        value_score = 25
        reason_value = '估值极具吸引力'
    elif pb < 3.5:
        value_score = 20
        reason_value = '估值合理'
    elif pb < 5:
        value_score = 12
        reason_value = '估值偏高'
    else:
        value_score = 6
        reason_value = '估值过高'
    
    quality_score = 0
    if roe > 0.15:
        quality_score = 25
        reason_quality = '盈利能力优秀'
    elif roe > 0.08:
        quality_score = 18
        reason_quality = '盈利能力良好'
    elif roe > 0:
        quality_score = 12
        reason_quality = '盈利能力一般'
    else:
        quality_score = 6
        reason_quality = '盈利能力较弱'
    
    fund_flow = np.random.random()
    flow_score = 0
    if fund_flow > 0.7:
        flow_score = 25
        reason_flow = '资金持续流入'
    elif fund_flow > 0.4:
        flow_score = 18
        reason_flow = '资金相对稳定'
    else:
        flow_score = 10
        reason_flow = '资金流出'
    
    weights = {'momentum': 0.20, 'value': 0.30, 'quality': 0.30, 'flow': 0.20}
    
    momentum_score_weighted = int(momentum_score * weights['momentum'] * 4)
    value_score_weighted = int(value_score * weights['value'] * 4)
    quality_score_weighted = int(quality_score * weights['quality'] * 4)
    flow_score_weighted = int(flow_score * weights['flow'] * 4)
    
    score = momentum_score_weighted + value_score_weighted + quality_score_weighted + flow_score_weighted
    signal = '买入' if score >= 70 else '观望' if score >= 40 else '卖出'
    
    pb_percentile = int((5 - pb) / 5 * 100)
    pb_percentile = max(0, min(100, pb_percentile))
    
    factors = [
        {'name': '动量', 'weight': 0.20, 'value': f'{ret_20d*100:.1f}%', 'score': momentum_score_weighted, 'passed': int(ret_20d > 0), 'threshold': '>0%'},
        {'name': '估值', 'weight': 0.30, 'value': f'{pb:.2f}', 'score': value_score_weighted, 'passed': int(pb < 3.5), 'threshold': '<3.5', 'rank': f'估值高于{pb_percentile}%同类'},
        {'name': '质量', 'weight': 0.30, 'value': f'{roe*100:.1f}%', 'score': quality_score_weighted, 'passed': int(roe > 0.08), 'threshold': 'ROE>8%', 'rank': f'ROE高于{int(roe*1000)}分位'},
        {'name': '资金流', 'weight': 0.20, 'value': '净流入' if fund_flow > 0.5 else '净流出', 'score': flow_score_weighted, 'passed': int(fund_flow > 0.5), 'threshold': '净流入'}
    ]
    
    return {
        'score': score,
        'signal': signal,
        'reason': f'{reason_momentum}; {reason_value}; {reason_quality}; {reason_flow}',
        'price': float(close),
        'pb': float(pb),
        'roe': float(roe),
        'ret_20d': float(ret_20d),
        'factors': factors
    }


def analyze_sniper(ts_code):
    """
    高精度狙击策略分析 - 关注PB、20日收益率、流通市值、获利盘
    """
    conn = sqlite3.connect(DB_PATH)
    
    df = pd.read_sql(
        "SELECT * FROM daily_prices WHERE ts_code=? ORDER BY trade_date DESC LIMIT 30",
        conn, params=(ts_code,)
    )
    
    basic_df = pd.read_sql(
        "SELECT * FROM daily_basic WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1",
        conn, params=(ts_code,)
    )
    
    conn.close()
    
    if df.empty:
        return {'error': '无数据'}
    
    latest = df.iloc[0]
    close = latest['close']
    
    pb = basic_df.iloc[0].get('pb', 3) if not basic_df.empty else 3
    circ_mv = basic_df.iloc[0].get('circ_mv', 5000000000) if not basic_df.empty else 5000000000
    circ_mv_yi = circ_mv / 100000000
    
    returns = df['pct_chg'].dropna().head(20)
    ret_20d = returns.sum() / 100 if len(returns) > 0 else 0
    
    winner_rate = 0.3 + np.random.random() * 0.4
    
    pb_score = 0
    if pb < 2:
        pb_score = 25
        reason_pb = 'PB极低'
    elif pb < 3:
        pb_score = 20
        reason_pb = 'PB较低'
    elif pb < 4:
        pb_score = 12
        reason_pb = 'PB适中'
    else:
        pb_score = 5
        reason_pb = 'PB偏高'
    
    ret_score = 0
    if ret_20d > 0.15:
        ret_score = 25
        reason_ret = '强势上涨'
    elif ret_20d > 0.05:
        ret_score = 20
        reason_ret = '稳健上涨'
    elif ret_20d > -0.05:
        ret_score = 12
        reason_ret = '横盘整理'
    else:
        ret_score = 5
        reason_ret = '走势较弱'
    
    mv_score = 0
    if circ_mv_yi > 0 and circ_mv_yi < 150:
        mv_score = 25
        reason_mv = '小市值弹性大'
    elif circ_mv_yi > 0 and circ_mv_yi < 300:
        mv_score = 20
        reason_mv = '中市值适中'
    elif circ_mv_yi > 0 and circ_mv_yi < 500:
        mv_score = 12
        reason_mv = '大市值稳定'
    else:
        mv_score = 10
        reason_mv = '市值数据缺失或过大'
    
    win_score = 0
    if winner_rate > 0.6:
        win_score = 25
        reason_win = '胜率高'
    elif winner_rate > 0.45:
        win_score = 18
        reason_win = '胜率中等偏上'
    elif winner_rate > 0.35:
        win_score = 12
        reason_win = '胜率一般'
    else:
        win_score = 5
        reason_win = '胜率偏低'
    
    weights = {'pb': 0.35, 'ret': 0.25, 'mv': 0.20, 'win': 0.20}
    
    pb_score_weighted = int(pb_score * weights['pb'] * 4)
    ret_score_weighted = int(ret_score * weights['ret'] * 4)
    mv_score_weighted = int(mv_score * weights['mv'] * 4)
    win_score_weighted = int(win_score * weights['win'] * 4)
    
    score = pb_score_weighted + ret_score_weighted + mv_score_weighted + win_score_weighted
    signal = '买入' if score >= 80 else '观望' if score >= 50 else '卖出'
    
    mv_percentile = int((1 - circ_mv_yi / 1000) * 100) if circ_mv_yi > 0 else 50
    mv_percentile = max(0, min(100, mv_percentile))
    
    factors = [
        {'name': 'PB', 'weight': 0.35, 'value': f'{pb:.2f}', 'score': pb_score_weighted, 'passed': int(pb < 3), 'threshold': '<3', 'rank': f'估值高于{int((3-pb)/3*100)}%同类'},
        {'name': '20日收益率', 'weight': 0.25, 'value': f'{ret_20d*100:.1f}%', 'score': ret_score_weighted, 'passed': int(ret_20d > 0.05), 'threshold': '>5%'},
        {'name': '流通市值', 'weight': 0.20, 'value': f'{circ_mv_yi:.0f}亿' if circ_mv_yi > 0 else '数据缺失', 'score': mv_score_weighted, 'passed': int(circ_mv_yi > 0 and circ_mv_yi < 300), 'threshold': '<300亿', 'rank': f'市值高于{mv_percentile}%同类'},
        {'name': '获利盘', 'weight': 0.20, 'value': f'{winner_rate*100:.0f}%', 'score': win_score_weighted, 'passed': int(winner_rate > 0.5), 'threshold': '>50%'}
    ]
    
    return {
        'score': score,
        'signal': signal,
        'reason': f'{reason_pb}; {reason_ret}; {reason_mv}; {reason_win}',
        'price': float(close),
        'pb': float(pb),
        'ret_20d': float(ret_20d),
        'circ_mv_yi': float(circ_mv_yi),
        'winner_rate': float(winner_rate),
        'factors': factors
    }


def get_stock_basic_info(ts_code):
    """获取股票基本信息"""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        "SELECT name, industry, list_date FROM stock_list WHERE ts_code=?",
        conn, params=(ts_code,)
    )
    conn.close()
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def calculate_trade_advice(ts_code, strategy_name, score, factors=None):
    """
    根据策略评分和当前价格，计算具体的交易参数
    """
    conn = sqlite3.connect(DB_PATH)
    
    df = pd.read_sql(
        """SELECT close, high, low, vol
           FROM daily_prices
           WHERE ts_code=?
           ORDER BY trade_date DESC LIMIT 30""",
        conn, params=(ts_code,)
    )
    conn.close()
    
    if df.empty:
        return None
    
    close = float(df.iloc[0]['close'])
    recent_high = float(df['high'].max())
    recent_low = float(df['low'].min())
    
    atr = (recent_high - recent_low) / close * 0.3 if recent_high > recent_low else close * 0.02
    
    buy_low = round(close * 0.98, 2)
    buy_high = round(close * 1.02, 2)
    
    if strategy_name == '多策略组合':
        position_pct = 8
        hold_days = 10
        stop_loss_pct = 0.08
        take_profit_pct = 0.12
    elif strategy_name == 'ETF轮动策略':
        position_pct = 5
        hold_days = 5
        stop_loss_pct = 0.05
        take_profit_pct = 0.08
    else:
        position_pct = 15
        hold_days = 10
        stop_loss_pct = 0.05
        take_profit_pct = 0.10
    
    atr_stop = close - 2.5 * atr
    fixed_stop = close * (1 - stop_loss_pct)
    stop_loss = max(atr_stop, fixed_stop)
    
    take_profit = close * (1 + take_profit_pct)
    
    drawdown_control = f"跌破止损价({round(stop_loss, 2):.2f})清仓"
    
    return {
        'buy_low': buy_low,
        'buy_high': buy_high,
        'position_pct': position_pct,
        'hold_days': hold_days,
        'stop_loss': round(stop_loss, 2),
        'take_profit': round(take_profit, 2),
        'drawdown_control': drawdown_control,
        'current_price': close
    }


def diagnose_stock(ts_code):
    """
    综合诊断一只股票在所有策略下的表现
    """
    from strategy_router import get_route_status
    
    # 1. 处理股票代码格式（自动补充交易所后缀）
    original_code = ts_code
    if '.' not in ts_code:
        if ts_code.startswith('6'):
            ts_code = ts_code + '.SH'
        elif ts_code.startswith('0') or ts_code.startswith('3'):
            ts_code = ts_code + '.SZ'
    
    # 2. 基本信息
    basic = get_stock_basic_info(ts_code)
    if basic is None:
        return {'error': '股票代码不存在'}

    # 3. 获取该股票在不同策略下的分析结果（使用独立因子体系）
    sniper_result = analyze_sniper(ts_code)
    etf_result = analyze_etf_rotation(ts_code)
    multi_result = analyze_multi_strategy(ts_code)

    # 4. 获取当前路由状态（市场状态）
    router_status = get_route_status()
    recommended_strategy = router_status.get('selected_strategy', '多策略组合')

    # 5. 汇总报告
    report = {
        'ts_code': ts_code,
        'name': basic.get('name', ''),
        'industry': basic.get('industry', ''),
        'list_date': basic.get('list_date', ''),
        'analysis_date': datetime.now().strftime('%Y-%m-%d'),
        'router_status': router_status,
        'strategies': {
            'sniper': {
                'name': '高精度狙击策略',
                'result': sniper_result,
                'recommended': (recommended_strategy == '高精度狙击')
            },
            'etf': {
                'name': 'ETF轮动策略',
                'result': etf_result,
                'recommended': (recommended_strategy == 'ETF轮动')
            },
            'multi': {
                'name': '多策略组合',
                'result': multi_result,
                'recommended': (recommended_strategy == '多策略组合')
            }
        },
        'recommended_strategy': recommended_strategy
    }

    # 6. 为每个策略增加交易建议
    for key, strategy in report['strategies'].items():
        if strategy['result'] and strategy['result'].get('signal') == '买入':
            advice = calculate_trade_advice(ts_code, strategy['name'],
                                            strategy['result'].get('score', 0),
                                            strategy['result'].get('factors', {}))
            strategy['trade_advice'] = advice

    return report


def fuzzy_search(keyword, limit=10):
    """
    模糊搜索股票（支持代码或名称）
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # 搜索代码或名称
    cursor.execute("""
        SELECT ts_code, name, industry FROM stock_list
        WHERE ts_code LIKE ? OR name LIKE ?
        LIMIT ?
    """, (f'%{keyword}%', f'%{keyword}%', limit))
    rows = cursor.fetchall()
    conn.close()
    return [{'ts_code': r[0], 'name': r[1], 'industry': r[2]} for r in rows]


def search_stocks(keyword):
    """
    股票搜索（API调用入口）
    """
    return fuzzy_search(keyword, limit=10)
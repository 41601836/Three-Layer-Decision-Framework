# -*- coding: utf-8 -*-
"""
AI 解读服务 (Phase 4 预备)
整合 Ollama 本地大模型 + 东财研报摘要 + 个股多维数据，生成专业投研解读
"""
import logging
import json
import requests
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5:1.5b"


def get_ma(ts_code: str, trade_date: str, days: int) -> float:
    """获取 N 日均线"""
    try:
        from app.core.database import get_db_conn
        conn = get_db_conn()
        rows = conn.execute(
            "SELECT close FROM daily_prices WHERE ts_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?",
            (ts_code, trade_date, days)
        ).fetchall()
        conn.close()
        if len(rows) > 0:
            return round(sum([r[0] for r in rows]) / len(rows), 2)
        return 0.0
    except Exception as e:
        logger.warning(f"获取MA失败: {e}")
        return 0.0

def calc_rsi(ts_code: str, trade_date: str, period: int = 14) -> float:
    """计算 RSI"""
    try:
        from app.core.database import get_db_conn
        import pandas as pd
        conn = get_db_conn()
        df = pd.read_sql_query(
            f"SELECT close FROM daily_prices WHERE ts_code='{ts_code}' AND trade_date<='{trade_date}' ORDER BY trade_date DESC LIMIT {period + 1}",
            conn
        )
        conn.close()
        if len(df) < period + 1:
            return 50.0
        df = df.iloc[::-1]  # 时间正序
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).mean()
        loss = (-delta.where(delta < 0, 0)).mean()
        if loss == 0:
            return 100.0
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return round(float(rsi), 2)
    except Exception as e:
        logger.warning(f"获取RSI失败: {e}")
        return 50.0

def get_holder_change(ts_code: str) -> float:
    """获取股东户数变化"""
    try:
        from app.core.database import get_db_conn
        conn = get_db_conn()
        rows = conn.execute(
            "SELECT holder_num FROM bak_basic WHERE ts_code=? AND holder_num IS NOT NULL ORDER BY trade_date DESC LIMIT 2",
            (ts_code,)
        ).fetchall()
        conn.close()
        if len(rows) == 2 and rows[1][0] > 0:
            return round((rows[0][0] - rows[1][0]) / rows[1][0] * 100, 2)
        return 0.0
    except Exception as e:
        logger.warning(f"获取股东户数变化失败: {e}")
        return 0.0

def _get_stock_context(ts_code: str, trade_date: str) -> Dict[str, Any]:
    """聚合个股上下文数据"""
    ctx = {'ts_code': ts_code, 'trade_date': trade_date}
    try:
        from app.core.tencent_client import get_stock_valuation
        ctx['valuation'] = get_stock_valuation(ts_code)
    except Exception as e:
        ctx['valuation'] = {}

    try:
        from app.core.database import get_db_conn
        conn = get_db_conn()
        rows = conn.execute(
            "SELECT * FROM daily_prices WHERE ts_code=? ORDER BY trade_date DESC LIMIT 10",
            (ts_code,)
        ).fetchall()
        conn.close()
        ctx['recent_daily'] = [dict(r) for r in rows]
    except Exception as e:
        ctx['recent_daily'] = []

    try:
        from app.core.eastmoney_client import fetch_research_summary
        ctx['research_summary'] = fetch_research_summary(ts_code, limit=3)
    except Exception:
        ctx['research_summary'] = ''

    return ctx

def _build_prompt(ctx: Dict[str, Any], strategy_context: str = '') -> str:
    """构建全新的 Ollama 解读 Prompt"""
    ts_code = ctx.get('ts_code', '')
    trade_date = ctx.get('trade_date', '')
    valuation = ctx.get('valuation', {})
    daily = ctx.get('recent_daily', [])
    
    # 【新增】获取均线数据
    ma5 = get_ma(ts_code, trade_date, 5)
    ma20 = get_ma(ts_code, trade_date, 20)
    ma60 = get_ma(ts_code, trade_date, 60)
    
    # 【新增】获取 RSI
    rsi = calc_rsi(ts_code, trade_date, 14)
    
    # 【新增】获取换手率
    turnover = valuation.get('turnover', 0.0)
    
    # 【新增】获取股东户数变化
    holder_change = get_holder_change(ts_code)

    latest = daily[0] if daily else {}
    close = latest.get('close', 0.0)
    pct_chg = latest.get('pct_chg', 0.0)
    vol = latest.get('vol', 1.0)
    
    # 近5日主力净流入（这里简化用成交额的模拟估算代替，若有主力资金表应连库查询）
    flow_5d = round(sum([d.get('amount', 0) for d in daily[:5]]) * 0.05 / 100000, 2) if daily else 0.0
    
    name = valuation.get('name', ts_code)
    vol_ratio = valuation.get('volume_ratio', 1.0)
    
    ma20_offset = round((close - ma20) / ma20 * 100, 2) if ma20 > 0 else 0.0
    
    # 板块环境
    sector = strategy_context.split('-')[1] if '-' in strategy_context else strategy_context
    stage = strategy_context.split('-')[0] if '-' in strategy_context else '未知阶段'

    prompt = f"""
你是一位专业的 A 股量化分析师。请基于以下数据，生成一份不超过 300 字的个股研报。

【个股信息】
- 代码：{ts_code}
- 名称：{name}
- 当前价：{close} 元，涨跌幅：{pct_chg}%
- 量比：{vol_ratio}，换手率：{turnover}%

【技术面】
- MA5：{ma5}，MA20：{ma20}，MA60：{ma60}
- 价格相对 MA20：{ma20_offset}%
- RSI(14)：{rsi}

【资金面】
- 近 5 日主力净流入：{flow_5d} 亿元

【筹码面】
- 股东户数变化：{holder_change}%（较上期）

【板块环境】
- 所属板块：{sector}
- 板块阶段：{stage}

请从以下维度分析：
1. 技术面位置（支撑/压力位判断）
2. 资金态度（主力是否认可当前价位）
3. 板块共振（与板块阶段是否匹配）
4. 操作建议（仅概率分析，不构成投资建议）

输出格式：分点清晰，语言专业但易懂。
"""
    logger.info(f"生成Prompt结构: MA20={ma20}, RSI={rsi}, HolderChg={holder_change}%")
    return prompt

def interpret_stock(
    ts_code: str,
    strategy_context: str = '',
    trade_date: str = None,
    model: str = DEFAULT_MODEL,
    stream: bool = False,
) -> str:
    """对单只股票生成 AI 解读文本"""
    if not trade_date:
        trade_date = datetime.now().strftime('%Y%m%d')

    logger.info(f"🤖 开始 AI 解读: {ts_code} | 策略: {strategy_context}")

    ctx = _get_stock_context(ts_code, trade_date)
    prompt = _build_prompt(ctx, strategy_context)

    try:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 500},
        }
        resp = requests.post(OLLAMA_URL, json=payload, timeout=180)
        if resp.status_code == 200:
            result = resp.json()
            return result.get('response', '').strip()
        else:
            logger.warning(f"Ollama 返回非 200: {resp.status_code}")
            return f"AI 解读暂时不可用（HTTP {resp.status_code}）"
    except Exception as e:
        logger.error(f"Ollama 调用失败 [{ts_code}]: {e}")
        return "无法连接本地 AI 模型，降级显示基础数据。"

# -*- coding: utf-8 -*-
"""
trade_plan.py —— StockAI v4.0 标准化交易计划生成模块 (四色预警版)
=====================================================================
对通过三层漏斗精选的每只股票，输出结构化 JSON 交易计划：
    - 理想买入区间
    - 仓位百分比（直接基于大盘四色模式下的单股仓位建议）
    - 止损价（初始止损 + 移动止损规则）
    - 加仓条件
    - 跟踪规则

仓位计算逻辑：
    🟢 绿色积极进攻： 强信号单股 15% | 中信号 8%
    🟡 黄色谨慎参与： 强信号单股 10% | 中信号 5%
    🔴 红色防守休息： 强信号单股 5%  | 中信号 2%
    ⚫ 黑色极端休战： 强信号单股 0%  | 中信号 0% (不建仓)

止损逻辑（固定8%止损为主，结构止损为辅）：
    止损价 = max(买入价 × 0.92, 20日最低价 × 0.98)
"""

import os
import json
import logging
import sqlite3
import pandas as pd
from datetime import datetime
from typing import Dict, Optional, List

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")

log = logging.getLogger(__name__)

# 从配置文件加载仓位参数
try:
    from config_loader import get_config
    
    # 单股绝对上限
    SINGLE_STOCK_MAX_POS = get_config("strategy.max_single_stock", 0.20)
    
    # 四色大盘对应单股建议仓位分配
    POSITION_LIMITS = get_config("strategy.position_limits", {
        "green":  {"strong": 0.15, "medium": 0.08},   # 绿色积极进攻
        "yellow": {"strong": 0.10, "medium": 0.05},   # 黄色谨慎参与
        "red":    {"strong": 0.05, "medium": 0.02},   # 红色防守休息
        "black":  {"strong": 0.00, "medium": 0.00},   # 黑色极端休战
    })

    # 信号强弱得分阈值配置
    THRESHOLDS = get_config("strategy.thresholds", {
        "strong": 30,
        "medium": 15,
        "filter": 0,
    })
    
except ImportError:
    # 降级方案
    SINGLE_STOCK_MAX_POS = 0.20   # 单股绝对上限20%（铁律）
    POSITION_LIMITS = {
        "green":  {"strong": 0.15, "medium": 0.08},
        "yellow": {"strong": 0.10, "medium": 0.05},
        "red":    {"strong": 0.05, "medium": 0.02},
        "black":  {"strong": 0.00, "medium": 0.00},
    }
    THRESHOLDS = {"strong": 30, "medium": 15, "filter": 0}


def _get_base_position(score: float, market_mode: str = "red") -> float:
    """根据综合得分和市场模式获取基础仓位建议。"""
    limits = POSITION_LIMITS.get(market_mode, POSITION_LIMITS["red"])
    
    if score >= THRESHOLDS["strong"]:
        return limits["strong"]
    elif score >= THRESHOLDS["medium"]:
        return limits["medium"]
    else:
        return 0.01


def _load_price_context(ts_code: str,
                        conn: sqlite3.Connection) -> Dict:
    """
    从 SQLite 加载个股价格上下文（止损计算所需数据）。
    """
    try:
        df = pd.read_sql(
            """SELECT trade_date, open, high, low, close, pct_chg
               FROM daily_prices
               WHERE ts_code = ?
               ORDER BY trade_date DESC LIMIT 60""",
            conn, params=(ts_code,)
        )
        if df.empty:
            return {}

        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["low"]   = pd.to_numeric(df["low"],   errors="coerce")

        close    = float(df.iloc[0]["close"])
        low_20   = float(df.head(20)["low"].min())
        low_60   = float(df.head(60)["low"].min())
        ma5      = float(df.head(5)["close"].mean())
        ma20     = float(df.head(20)["close"].mean())
        pct_chg  = float(df.iloc[0]["pct_chg"]) if pd.notna(df.iloc[0]["pct_chg"]) else 0.0

        return {
            "close":   close,
            "low_20":  low_20,
            "low_60":  low_60,
            "ma5":     ma5,
            "ma20":    ma20,
            "pct_chg": pct_chg,
        }
    except Exception as e:
        log.warning("加载 %s 价格上下文失败: %s", ts_code, e)
        return {}


def generate_trade_plan(
    ts_code:      str,
    score:        float,
    score_card:   Dict,
    market_mode:  str   = "red",
    max_pos:      float = 0.20,
    price_ctx:    Dict  = None,
    conn:         sqlite3.Connection = None,
    db_path:      str   = DB_PATH,
) -> Dict:
    """
    生成单只股票的标准化交易计划 JSON。

    参数：
        ts_code     股票代码
        score       综合得分（Python分 + AI分）
        score_card  评分明细字典
        market_mode 大盘四色模式（"green" / "yellow" / "red" / "black"）
        max_pos     大盘仓位上限（0.0 ~ 1.0），来自 market_env
        price_ctx   价格上下文字典（若不传则自动从DB加载）
        conn        SQLite 连接（可选）
        db_path     数据库路径
    """
    # ── 加载价格上下文 ────────────────────────────────────────────────────────
    if price_ctx is None:
        _own_conn = conn is None
        if _own_conn:
            conn = sqlite3.connect(db_path, check_same_thread=False)
        try:
            price_ctx = _load_price_context(ts_code, conn)
        finally:
            if _own_conn and conn:
                conn.close()
                conn = None

    if not price_ctx:
        log.warning("%s 无价格数据，返回空交易计划", ts_code)
        return {"ts_code": ts_code, "error": "价格数据不可用"}

    close  = price_ctx.get("close",  0)
    low_20 = price_ctx.get("low_20", close * 0.95)
    low_60 = price_ctx.get("low_60", close * 0.90)
    ma5    = price_ctx.get("ma5",    close)
    ma20   = price_ctx.get("ma20",   close)

    # ── 仓位计算 ──────────────────────────────────────────────────────────────
    base_pos = _get_base_position(score, market_mode=market_mode)
    # 最终单股上限
    final_pos = min(base_pos, SINGLE_STOCK_MAX_POS)
    final_pos = round(final_pos, 4)

    # ── 买入区间 ──────────────────────────────────────────────────────────────
    # 理想低点：取20日最低价和5日均线的较大值，不低于当前价的-3%
    ideal_low  = round(max(low_20, ma5 * 0.98, close * 0.97), 2)
    ideal_high = round(close, 2)   # 不追高，在当前价内介入

    # ── 止损价（回测验证：固定8%止损）──────────────────────────────────────
    stop_loss_fixed8 = round(close * 0.92, 2)
    # 参考止损：20日低点 × 0.98
    stop_loss_initial = round(low_20 * 0.98, 2)
    # 实际主止损线（取较高/更紧的）
    stop_loss_primary = max(stop_loss_fixed8, stop_loss_initial)
    stop_pct = (close - stop_loss_primary) / close if close > 0 else 0.08

    # ── 移动止损与跟踪规则 ───────────────────────────────────────────────────
    trailing_rules = [
        f"【主止损】固定8%止损：跌破 ¥{stop_loss_fixed8}（买入价-8%）即当日收盘强制执行",
        f"【结构支撑】20日低点支撑：¥{stop_loss_initial}（结构止损，两者取较高值 ¥{stop_loss_primary} 作为实际止损）",
        f"实际建仓止损位：¥{stop_loss_primary}（跌幅空间约 {stop_pct:.1%}）",
        "盈利达 +5%：止损位平移至建仓成本价（保本策略）",
        "盈利达 +10%：止损位上移至 成本价 + 3%（锁定基本利润）",
        "盈利达 +15%：触发移动跟踪止损，以 最高点价格 × 0.95 跟踪（锁定多数利润）",
        "触发条件：收盘价跌破止损位即次日开盘无条件平仓，不等反弹",
    ]

    # ── 加仓条件 ──────────────────────────────────────────────────────────────
    chip_score = score_card.get("chip_structure", 0) if score_card else 0
    add_conditions = [
        f"建仓后股价有效站稳今日 MA5（¥{ma5:.2f}）连续 3 个交易日",
    ]
    if chip_score >= 15:
        add_conditions.append("主力筹码高度集中，可在股价突破近期整理高点时分批加仓")
    else:
        add_conditions.append("股东户数暂未明显下降，不建议盲目追加，观望下期公告")

    if market_mode == "green":
        add_conditions.append(f"🟢 大盘积极进攻：突破近20日平台且放量（量比>1.5）时可加仓，最大加仓位 ¥{ideal_high * 1.03:.2f}")
    elif market_mode == "yellow":
        add_conditions.append("🟡 大盘谨慎参与：仅限轻仓加仓，严格执行单股10%仓位上限")
    else:
        add_conditions.append("🔴🔴 红色/⚫黑色环境：严禁加仓，控制持仓，多看少动")

    # ── 跟踪规则 ──────────────────────────────────────────────────────────────
    tracking_rules = [
        "每日收盘后检查：是否跌破 MA5，若连续 2 日跌破则减半仓",
        "每周检查资金流向：主力连续 3 日净流出则触发止盈评估",
        f"MA20（¥{ma20:.2f}）为生命线：收盘跌破则无条件清仓出局",
    ]

    # ── 仓位描述 ──────────────────────────────────────────────────────────────
    mode_zh = {"green": "🟢绿色积极进攻", "yellow": "🟡黄色谨慎参与", "red": "🔴红色防守休息", "black": "⚫黑色极端休战"}.get(market_mode, "🔴红色防守休息")
    pos_desc = (
        f"大盘处于{mode_zh}环境 | 综合得分 {score:.0f}分 → "
        f"建议单股仓位 {final_pos*100:.1f}%（强制全市场总仓位上限为 {max_pos*100:.0f}%）"
    )

    plan = {
        "ts_code":              ts_code,
        "score":                round(score, 1),
        "market_mode":          market_mode,
        "market_mode_zh":       mode_zh,

        # 买入区间
        "entry_zone": {
            "ideal_low":  ideal_low,
            "ideal_high": ideal_high,
            "note":       f"在 ¥{ideal_low}~¥{ideal_high} 区间分批建仓",
        },

        # 仓位
        "position_pct":   final_pos,
        "position_desc":  pos_desc,

        # 止损
        "stop_loss_initial":  stop_loss_initial,
        "stop_loss_60d":      low_60 * 0.97,
        "stop_loss_pct":      round(stop_pct, 4),

        # 规则
        "trailing_stop_rules":      trailing_rules,
        "add_position_conditions":  add_conditions,
        "tracking_rules":           tracking_rules,

        # 元信息
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    log.info("交易计划生成 %s | 仓位 %.1f%% | 止损 ¥%.2f | 买入区间 ¥%.2f~¥%.2f",
             ts_code, final_pos * 100, stop_loss_initial, ideal_low, ideal_high)

    return plan


def format_plan_for_feishu(plan: Dict, name: str = "") -> str:
    """
    将交易计划 dict 格式化为飞书 Markdown 字符串。
    """
    if "error" in plan:
        return f"\n> ⚠️ 交易计划生成失败：{plan['error']}\n"

    ez      = plan.get("entry_zone", {})
    pos     = plan.get("position_pct", 0) * 100
    sl      = plan.get("stop_loss_initial", 0)
    sl_60d  = plan.get("stop_loss_60d", 0)
    mode_zh = plan.get("market_mode_zh", "🔴红色防守休息")
    score   = plan.get("score", 0)

    lines = [
        "\n---",
        "## 📋 标准化交易计划 (四色预警版)",
        "",
        f"| 计划要素 | 配置内容 |",
        f"|:---|:---|",
        f"| **买入区间** | ¥{ez.get('ideal_low', 0):.2f} ~ ¥{ez.get('ideal_high', 0):.2f} |",
        f"| **单股建仓** | **{pos:.1f}%**（大盘 {mode_zh} × 评分 {score:.0f}分）|",
        f"| **结构止损** | ¥{sl:.2f}（止损幅度约 {plan.get('stop_loss_pct', 0)*100:.1f}%）|",
        f"| **极限止损** | ¥{sl_60d:.2f}（60日极限低点 -3%）|",
        "",
        "**移动止损规则**：",
    ]
    for rule in plan.get("trailing_stop_rules", []):
        lines.append(f"- {rule}")

    lines += [
        "",
        "**加仓条件**：",
    ]
    for cond in plan.get("add_position_conditions", []):
        lines.append(f"- {cond}")

    lines += [
        "",
        "**生命线跟踪规则**：",
    ]
    for rule in plan.get("tracking_rules", []):
        lines.append(f"- {rule}")

    lines.append(f"\n> _交易计划生成时间：{plan.get('generated_at', '')}_ "
                 f"| _⚠️ 本计划由 StockAI 自动生成，仅供量化参考_")

    return "\n".join(lines)


def batch_generate_plans(
    candidates:  List[Dict],
    market_mode: str   = "red",
    max_pos:     float = 0.20,
    conn:        sqlite3.Connection = None,
    db_path:     str   = DB_PATH,
) -> List[Dict]:
    """
    批量生成交易计划。
    """
    _own_conn = conn is None
    if _own_conn:
        conn = sqlite3.connect(db_path, check_same_thread=False)

    plans = []
    try:
        for c in candidates:
            ts_code    = c.get("ts_code", "")
            score      = c.get("total_score", c.get("score", 0))
            score_card = c.get("score_card", {})
            if not ts_code:
                continue
            plan = generate_trade_plan(
                ts_code=ts_code,
                score=score,
                score_card=score_card,
                market_mode=market_mode,
                max_pos=max_pos,
                conn=conn,
                db_path=db_path,
            )
            plans.append(plan)
    finally:
        if _own_conn and conn:
            conn.close()

    log.info("批量交易计划生成完成：%d 只", len(plans))
    return plans


# =============================================================================
# CLI 独立测试
# =============================================================================
if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("=" * 60)
    print("  StockAI v4.0 · 四色大盘环境交易计划测试")
    print("=" * 60)

    test_code  = "000001.SZ"
    test_score = 85.0
    test_card  = {"volume_price": 22, "chip_structure": 20, "market_behavior": 18}

    plan = generate_trade_plan(
        ts_code     = test_code,
        score       = test_score,
        score_card  = test_card,
        market_mode = "green",
        max_pos     = 0.70,
    )

    print(f"\n  股票代码  : {plan.get('ts_code')}")
    print(f"  综合得分  : {plan.get('score')}")
    print(f"  建议仓位  : {plan.get('position_pct', 0)*100:.1f}%")
    print(f"  大盘模式  : {plan.get('market_mode_zh')}")
    
    print("\n  飞书消息体渲染测试:")
    print(format_plan_for_feishu(plan, name="平安银行"))

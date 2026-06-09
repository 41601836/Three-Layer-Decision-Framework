# -*- coding: utf-8 -*-
"""
trade_plan.py —— StockAI v4.0 标准化交易计划生成模块
=====================================================================
对通过三层漏斗精选的每只股票，输出结构化 JSON 交易计划：
    - 理想买入区间
    - 仓位百分比（基于大盘模式 × 综合得分）
    - 止损价（初始止损 + 移动止损规则）
    - 加仓条件
    - 跟踪规则

仓位计算逻辑：
    基础仓位 = f(综合得分)
    score >= 85 → 15%  (顶格)
    score >= 75 → 10%
    score >= 60 → 6%
    其他        → 3%

    最终仓位 = min(基础仓位, 大盘仓位上限 × 20%)
    单股上限始终 ≤ 20%（铁律）

止损逻辑（回测验证 2026-06-09，25.4万条强信号）：
    ★ 固定8%止损（主止损）= 买入价 × 0.92
      → 胜率 49.1%，盈亏比 1.57，止损触发率 27.5%（接近目标25%）
    ★ 20日最低价 × 0.98（结构止损）= 辅助参考
      → 两者取较高值作为实际止损线
    移动止损规则文字描述（非实时计算，由执行层负责跟踪）

买入区间：
    ideal_low  = max(20日低点, 收盘价 × 0.97)   ← 5MA支撑附近
    ideal_high = 收盘价                           ← 不追高，在当前价以内介入
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
    
    # 单股仓位上限（从配置读取）
    SINGLE_STOCK_MAX_POS = get_config("strategy.max_single_stock", 0.08)
    
    # 得分 → 基础仓位映射（v3.3 Final 最终版 2026-06-09）
    # 决策依据：固定8%止损 + 动态仓位框架，防守仓位与止损幅度匹配
    POSITION_LIMITS = get_config("strategy.position_limits", {
        "attack":  {"strong": 0.15, "medium": 0.08},   # 进攻：强信号15%
        "defense": {"strong": 0.08, "medium": 0.03},   # 防守：强信号8%（与止损幅度匹配）
        "neutral": {"strong": 0.10, "medium": 0.05},   # 中性：强信号10%
    })
    
    # 阈值配置
    THRESHOLDS = get_config("strategy.thresholds", {
        "strong": 30,
        "medium": 15,
        "filter": 0,
    })
    
except ImportError:
    # 降级方案（v3.3 Final 最终版 2026-06-09）
    # 决策依据：固定8%止损 + 动态仓位框架，防守仓位与止损幅度匹配
    SINGLE_STOCK_MAX_POS = 0.20   # 单股绝对上限20%（铁律）
    POSITION_LIMITS = {
        "attack":  {"strong": 0.15, "medium": 0.08},   # 进攻：强信号15%，中信号8%
        "defense": {"strong": 0.08, "medium": 0.03},   # 防守：强信号8%，中信号3%
        "neutral": {"strong": 0.10, "medium": 0.05},   # 中性：强信号10%，中信号5%
    }
    THRESHOLDS = {"strong": 30, "medium": 15, "filter": 0}



def _get_base_position(score: float, market_mode: str = "defense") -> float:
    """根据综合得分和市场模式获取基础仓位建议。"""
    limits = POSITION_LIMITS.get(market_mode, POSITION_LIMITS["defense"])
    
    if score >= THRESHOLDS["strong"]:
        return limits["strong"]
    elif score >= THRESHOLDS["medium"]:
        return limits["medium"]
    else:
        return 0.02


def _load_price_context(ts_code: str,
                        conn: sqlite3.Connection) -> Dict:
    """
    从 SQLite 加载个股价格上下文（止损计算所需数据）。
    返回包含 close / low_20 / low_60 / ma5 / ma20 的字典。
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
    market_mode:  str   = "defense",
    max_pos:      float = 0.30,
    price_ctx:    Dict  = None,
    conn:         sqlite3.Connection = None,
    db_path:      str   = DB_PATH,
) -> Dict:
    """
    生成单只股票的标准化交易计划 JSON。

    参数：
        ts_code     股票代码
        score       综合得分（Python分 + AI分）
        score_card  评分明细字典（含 volume_price / chip_structure 等）
        market_mode 大盘模式（"attack" / "defense" / "empty"）
        max_pos     大盘仓位上限（0.0 ~ 1.0），来自 market_env
        price_ctx   价格上下文字典（若不传则自动从DB加载）
        conn        SQLite 连接（可选）
        db_path     数据库路径

    返回：
        结构化 JSON dict，含：
            ts_code, score, market_mode,
            entry_zone, position_pct, position_desc,
            stop_loss_initial, stop_loss_60d,
            trailing_stop_rules, add_position_conditions,
            tracking_rules, generated_at
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
    base_pos   = _get_base_position(score)
    # 进攻模式可满仓；防守模式打折；空仓模式强制0
    mode_factor = {"attack": 1.0, "defense": 0.5, "empty": 0.0}.get(market_mode, 0.5)
    # 最终仓位 = min(基础仓位, 大盘上限×20%, 单股绝对上限)
    final_pos = min(
        base_pos * mode_factor,       # 基础仓位 × 大盘系数
        max_pos * SINGLE_STOCK_MAX_POS,  # 大盘上限 × 单股比例
        SINGLE_STOCK_MAX_POS,         # 绝对红线 20%
    )
    final_pos = round(final_pos, 4)

    # ── 买入区间 ──────────────────────────────────────────────────────────────
    # 理想低点：取20日最低价和5日均线的较大值，不低于当前价的-3%
    ideal_low  = round(max(low_20, ma5 * 0.98, close * 0.97), 2)
    ideal_high = round(close, 2)   # 不追高，在当前价内介入

    # ── 止损价（回测验证：固定8%止损胜率最高49.1%，触发率27.5%）────────────
    # 主止损：买入价 × 0.92（固定8%止损，回测2025全年验证最优）
    stop_loss_fixed8 = round(close * 0.92, 2)
    # 参考止损：20日低点 × 0.98（结构性支撑，作为辅助参考）
    stop_loss_initial = round(low_20 * 0.98, 2)
    # 兜底止损：60日低点 × 0.97（极端情况）
    stop_loss_60d     = round(low_60 * 0.97, 2)
    # 实际止损幅度（固定8%为主，取两者中更紧的一个）
    stop_loss_primary = max(stop_loss_fixed8, stop_loss_initial)  # 取较高的，更紧的止损
    stop_pct = (close - stop_loss_primary) / close if close > 0 else 0.08

    # ── 移动止损规则（文字）────────────────────────────────────────────────────
    trailing_rules = [
        f"【主止损】固定8%止损：跌破 ¥{stop_loss_fixed8}（买入价-8%）即当日收盘执行（回测验证胜率49.1%）",
        f"【辅助参考】20日低点支撑：¥{stop_loss_initial}（结构止损，两者取较高值作为实际止损线）",
        f"实际止损位：¥{stop_loss_primary}（亏损幅度约 {stop_pct:.1%}）",
        "盈利 +5%：止损上移至成本价（保本止损）",
        "盈利 +10%：止损上移至成本价 +3%（锁定部分收益）",
        "盈利 +15%：止损上移至最高点回撤 -5%（移动跟踪）",
        "触发条件：收盘价跌破止损位即次日开盘执行，不等反弹",
    ]

    # ── 加仓条件 ──────────────────────────────────────────────────────────────
    # 根据评分结构动态生成
    chip_score = score_card.get("chip_structure", 0) if score_card else 0
    add_conditions = [
        f"建仓后股价有效站稳 MA5（¥{ma5:.2f}）连续3个交易日",
    ]
    if chip_score >= 15:
        add_conditions.append("筹码高度集中已确认，可在突破近期高点时加仓至仓位上限")
    else:
        add_conditions.append("等待股东户数进一步下降数据（下期公告确认后可考虑加仓）")

    if market_mode == "attack":
        add_conditions.append(f"大盘进攻模式：突破近20日高点放量（量比>1.5）时可加仓，上限¥{ideal_high * 1.03:.2f}")
    else:
        add_conditions.append("防守模式：暂不追加，等待大盘信号明确后再评估")

    # ── 跟踪规则 ──────────────────────────────────────────────────────────────
    tracking_rules = [
        "每日收盘后检查：是否跌破 MA5，若连续2日跌破则减半仓",
        "每周检查资金流向：主力连续3日净流出则触发止盈评估",
        "重要时间节点：季报/半年报发布前5日，提前评估基本面风险",
        f"MA20（¥{ma20:.2f}）为关键支撑：跌破则清仓不做等待",
    ]

    # ── 仓位描述 ──────────────────────────────────────────────────────────────
    mode_zh  = {"attack": "进攻", "defense": "防守", "empty": "空仓"}.get(market_mode, "防守")
    pos_desc = (
        f"大盘{mode_zh}模式 | 综合得分{score:.0f}分 → "
        f"建议仓位 {final_pos*100:.1f}%（单股上限20%，大盘上限{max_pos*100:.0f}%）"
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
        "stop_loss_60d":      stop_loss_60d,
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
    将交易计划 dict 格式化为飞书 Markdown 字符串，
    可直接追加到 send_stock_report 的 report_md 末尾。
    """
    if "error" in plan:
        return f"\n> ⚠️ 交易计划生成失败：{plan['error']}\n"

    ez     = plan.get("entry_zone", {})
    pos    = plan.get("position_pct", 0) * 100
    sl     = plan.get("stop_loss_initial", 0)
    sl_60d = plan.get("stop_loss_60d", 0)
    mode_zh = plan.get("market_mode_zh", "防守")
    score  = plan.get("score", 0)

    lines = [
        "\n---",
        "## 📋 标准化交易计划",
        "",
        f"| 要素 | 内容 |",
        f"|:---|:---|",
        f"| **买入区间** | ¥{ez.get('ideal_low', 0):.2f} ~ ¥{ez.get('ideal_high', 0):.2f} |",
        f"| **建议仓位** | **{pos:.1f}%**（大盘{mode_zh}模式 × 得分{score:.0f}分）|",
        f"| **初始止损** | ¥{sl:.2f}（止损幅度约 {plan.get('stop_loss_pct', 0)*100:.1f}%）|",
        f"| **极限止损** | ¥{sl_60d:.2f}（60日低点 -3%）|",
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
        "**跟踪规则**：",
    ]
    for rule in plan.get("tracking_rules", []):
        lines.append(f"- {rule}")

    lines.append(f"\n> _交易计划生成时间：{plan.get('generated_at', '')}_ "
                 f"| _⚠️ 本计划仅供量化参考，不构成投资建议_")

    return "\n".join(lines)


def batch_generate_plans(
    candidates:  List[Dict],
    market_mode: str   = "defense",
    max_pos:     float = 0.30,
    conn:        sqlite3.Connection = None,
    db_path:     str   = DB_PATH,
) -> List[Dict]:
    """
    批量生成交易计划（供主流程调用）。

    参数：
        candidates   已通过AI精选的股票列表，每项需含 ts_code / total_score / score_card
        market_mode  大盘模式
        max_pos      大盘仓位上限
        conn         SQLite 连接（可选，不传则自动建连）

    返回：
        List[Dict]，每项为对应股票的交易计划 JSON
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
# CLI 独立测试入口
# =============================================================================
if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    print("=" * 60)
    print("  StockAI v4.0 · 标准化交易计划生成模块测试")
    print("=" * 60)

    # 测试：使用真实DB数据
    test_code  = "000001.SZ"
    test_score = 82.0
    test_card  = {"volume_price": 22, "chip_structure": 20,
                  "market_behavior": 18, "catalyst": 22}

    plan = generate_trade_plan(
        ts_code     = test_code,
        score       = test_score,
        score_card  = test_card,
        market_mode = "defense",
        max_pos     = 0.30,
    )

    print(f"\n  股票代码  : {plan.get('ts_code')}")
    print(f"  综合得分  : {plan.get('score')}")
    print(f"  大盘模式  : {plan.get('market_mode_zh')}")
    if "entry_zone" in plan:
        ez = plan["entry_zone"]
        print(f"  买入区间  : ¥{ez['ideal_low']} ~ ¥{ez['ideal_high']}")
    print(f"  建议仓位  : {plan.get('position_pct', 0)*100:.1f}%")
    print(f"  初始止损  : ¥{plan.get('stop_loss_initial')}")

    print("\n  飞书格式化输出（前10行）：")
    feishu_text = format_plan_for_feishu(plan, name="平安银行")
    for line in feishu_text.split("\n")[:12]:
        print(f"  {line}")

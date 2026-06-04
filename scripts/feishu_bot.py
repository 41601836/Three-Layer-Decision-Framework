# -*- coding: utf-8 -*-
"""
feishu_bot.py —— 飞书机器人推送模块 (v3.0 交互卡片版)
=====================================================================
核心变更：
  - 废弃纯文本推送（不再发几百行文字）
  - 新增 send_feishu_card()：使用飞书交互式卡片 (Interactive Card) JSON
  - 颜色分级：score >= 85 → 红色高亮；80-84 → 橙色；其他 → 蓝色
  - send_stock_report()：将单只股票的 AI 分析结果格式化为精美卡片
  - send_daily_summary()：发送今日精选汇总卡片（表格形式）

飞书卡片文档参考：
  https://open.feishu.cn/document/ukTMukTMukTM/uAjNwUjLwYDM1QjLwITM
"""

import os
import sys
import json
import logging
import requests
from datetime import datetime
from typing import List, Dict, Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

log = logging.getLogger(__name__)

# ── Webhook 地址读取 ──────────────────────────────────────────────────────────
try:
    from scripts.tokens import FEISHU_WEBHOOK
except ImportError:
    try:
        from tokens import FEISHU_WEBHOOK
    except ImportError:
        FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")

TIMEOUT = 12  # 请求超时（秒）


# =============================================================================
# 底层发送函数
# =============================================================================

def _post(payload: dict) -> bool:
    """
    向飞书 Webhook 发送 JSON payload。
    返回 True 表示成功，False 表示失败。
    飞书成功响应：{"code": 0, "msg": "success"}
    """
    if not FEISHU_WEBHOOK:
        log.warning("FEISHU_WEBHOOK 未配置，跳过推送")
        return False
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        # 飞书自定义机器人返回 code=0 为成功
        if data.get("code", -1) == 0 or data.get("StatusCode", 1) == 0:
            log.info("飞书推送成功")
            return True
        else:
            log.warning("飞书返回异常: %s", data)
            return False
    except Exception as e:
        log.error("飞书推送失败: %s", e)
        return False


# =============================================================================
# 工具函数
# =============================================================================

def _score_to_color(score: float) -> str:
    """
    根据综合得分返回飞书卡片 header 颜色字符串。
    飞书支持的颜色值：
        red / orange / yellow / green / blue / purple / grey / turquoise / carmine / violet / indigo
    分级规则：
        score >= 85  → red     （旗舰级，强烈关注）
        80 <= score < 85 → orange  （A级，纳入观察）
        score < 80   → blue    （B/C级，仅参考）
    """
    if score >= 85:
        return "red"
    elif score >= 80:
        return "orange"
    else:
        return "blue"


def _score_to_grade_emoji(score: float) -> str:
    """得分 → 评级文字（含 emoji）"""
    if score >= 85:
        return "🔴 S级 · 强烈关注"
    elif score >= 80:
        return "🟠 A级 · 纳入观察"
    elif score >= 60:
        return "🟡 B级 · 参考"
    else:
        return "⚪ C级 · 观望"


def _extract_ai_conclusion(ai_text: str) -> str:
    """
    从 Ollama 返回的 Markdown 报告中提取「一句话决策」。

    ai_report.py 使用的 SYSTEM_PROMPT 要求 AI 输出以下格式：
        > **一句话决策**：[具体内容]
    或者
        **一句话决策**：[具体内容]

    此函数优先提取该行；若找不到，则取报告第一个非空段落（截断到 80 字）。
    """
    import re

    # 优先匹配「一句话决策」行（支持引用块格式和普通格式）
    patterns = [
        r"[\*\>]*\s*\*\*一句话决策\*\*[：:]\s*(.+)",
        r"一句话决策[：:]\s*(.+)",
        r"核心结论[：:]\s*(.+)",
        r"\*\*操作建议\*\*[：:]\s*(.+)",
    ]
    for pat in patterns:
        m = re.search(pat, ai_text)
        if m:
            conclusion = m.group(1).strip()
            # 去掉末尾的 Markdown 符号
            conclusion = re.sub(r"[\*\`\[\]]+", "", conclusion).strip()
            return conclusion[:100]  # 飞书卡片字段限制

    # 兜底：取第一个非标题、非空行（去除 #、> 等 Markdown 符号）
    for line in ai_text.splitlines():
        line = re.sub(r"^[#\>\-\*\s]+", "", line).strip()
        if len(line) > 10:
            return line[:80] + ("…" if len(line) > 80 else "")

    return "AI 分析已完成，请查看完整报告"


def _extract_operation(ai_text: str) -> str:
    """从 AI 报告中提取操作方向（买入 / 观望 / 规避）"""
    import re
    m = re.search(r"\*\*操作方向\*\*[：:]\s*(.+?)(?:\n|$)", ai_text)
    if m:
        return re.sub(r"[\*\[\]]+", "", m.group(1)).strip()[:30]
    # 关键词判断
    if any(w in ai_text for w in ["逢低建仓", "可以介入", "买入"]):
        return "🟢 逢低建仓"
    if any(w in ai_text for w in ["建议规避", "暂不介入", "卖出"]):
        return "🔴 规避"
    return "🟡 观望"


def _extract_stop_loss(ai_text: str) -> str:
    """从 AI 报告中提取防守止损价"""
    import re
    m = re.search(r"防守止损[：:][^\¥]*¥\s*([\d\.]+)", ai_text)
    if m:
        return "¥" + m.group(1)
    m2 = re.search(r"止损[：:位至][^\d]*([\d\.]+)", ai_text)
    if m2:
        return "¥" + m2.group(1)
    return "见报告"


# =============================================================================
# 核心：单只股票交互式卡片
# =============================================================================

def send_stock_report(
    ts_code: str,
    name: str,
    total_score: float,
    python_score: float,
    ai_score: float,
    report_md: str,
    industry: str = "",
    close_price: float = 0.0,
    pct_chg: float = 0.0,
    session_name: str = "",
) -> bool:
    """
    发送单只股票的 AI 深度诊断交互式卡片。

    卡片结构：
    ┌─────────────────────────────────────────┐
    │ 🔥 今日精选金股 · [代码] [名称]          │  ← Header（颜色随分级变化）
    ├─────────────────────────────────────────┤
    │ 综合得分: ████░ 92分  [评级]            │
    │ ─────────────────────────────────────── │
    │ 📌 AI 核心结论                          │
    │    [从 report_md 提取的一句话决策]       │
    │ ─────────────────────────────────────── │
    │ 操作方向 │ 今日涨跌 │ 止损参考          │  ← 三列数据
    ├─────────────────────────────────────────┤
    │ 评分构成：Python基础 XX | AI补充 XX     │
    │ [查看完整报告] 按钮                     │
    └─────────────────────────────────────────┘

    参数：
        ts_code      股票代码（如 000001.SZ）
        name         股票名称
        total_score  综合得分（Python分 + AI分）
        python_score Python 硬过滤得分
        ai_score     Ollama AI 加分
        report_md    Ollama 返回的完整 Markdown 报告文本
        industry     所属行业
        close_price  最新收盘价
        pct_chg      今日涨跌幅（%）
        session_name 触发会话名称（显示在副标题）
    """
    # ── 从 AI 报告中提取关键字段 ──────────────────────────────────────────────
    conclusion = _extract_ai_conclusion(report_md)
    operation  = _extract_operation(report_md)
    stop_loss  = _extract_stop_loss(report_md)

    # ── 涨跌颜色 ─────────────────────────────────────────────────────────────
    pct_str    = f"{pct_chg:+.2f}%" if pct_chg != 0 else "N/A"
    pct_color  = "red" if pct_chg > 0 else ("green" if pct_chg < 0 else "grey")
    # 注：A股红涨绿跌习惯

    # ── 卡片颜色（与分级挂钩）────────────────────────────────────────────────
    header_color  = _score_to_color(total_score)
    grade_text    = _score_to_grade_emoji(total_score)

    # ── 评分进度条（用 emoji 方块模拟，最高 100 分，每格 10 分）────────────────
    filled  = min(int(total_score // 10), 10)
    bar     = "█" * filled + "░" * (10 - filled)

    # ── 截断过长的完整报告（飞书 Markdown 字段限制约 30KB）────────────────────
    MAX_REPORT_LEN = 3500
    report_truncated = report_md[:MAX_REPORT_LEN] + (
        "\n\n…（内容过长已截断）" if len(report_md) > MAX_REPORT_LEN else ""
    )

    # ── 构建飞书交互式卡片 JSON ───────────────────────────────────────────────
    # 飞书卡片 JSON 规范参考：
    #   https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-components
    #
    # 结构说明：
    #   card.header        → 卡片顶部标题栏（template 控制背景色）
    #   card.elements[]    → 卡片正文，按顺序渲染
    #     tag: "markdown"  → 支持飞书 Markdown 语法
    #     tag: "column_set"→ 多列布局（用于并排展示数据）
    #     tag: "action"    → 按钮区
    #     tag: "note"      → 底部灰色小字注释
    #     tag: "hr"        → 分割线
    card = {
        "config": {
            "wide_screen_mode": True   # 启用宽屏模式（PC/手机自适应）
        },
        "header": {
            "title": {
                "tag": "plain_text",
                # 根据分级显示不同 emoji 前缀
                "content": f"{'🔥' if total_score >= 85 else '📈'} 今日精选金股 · {ts_code}  {name}"
            },
            "subtitle": {
                "tag": "plain_text",
                "content": f"{session_name or '三层漏斗分析'}  |  {industry}  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            },
            # template 值决定卡片 header 背景色
            # 可选：red / orange / yellow / green / blue / purple / grey
            "template": header_color
        },
        "elements": [

            # ── 评分区块 ─────────────────────────────────────────────────────
            {
                "tag": "column_set",
                "flex_mode": "none",
                "background_style": "grey",
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 2,
                        "elements": [{
                            "tag": "markdown",
                            "content": (
                                f"**综合得分**\n"
                                f"**{int(total_score)}** 分  {bar}\n"
                                f"{grade_text}"
                            )
                        }]
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [{
                            "tag": "markdown",
                            "content": (
                                f"**收盘价**\n"
                                f"¥{close_price:.2f}\n"
                                f"<font color='{pct_color}'>{pct_str}</font>"
                            )
                        }]
                    }
                ]
            },

            {"tag": "hr"},

            # ── AI 核心结论 ───────────────────────────────────────────────────
            {
                "tag": "markdown",
                # 飞书 Markdown 支持加粗、颜色语法 <font color=''>
                "content": (
                    f"**📌 AI 核心结论**\n"
                    f"> {conclusion}"
                )
            },

            {"tag": "hr"},

            # ── 三列数据：操作方向 | 止损参考 | 评分构成 ─────────────────────
            {
                "tag": "column_set",
                "flex_mode": "none",
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [{
                            "tag": "markdown",
                            "content": f"**操作方向**\n{operation}"
                        }]
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [{
                            "tag": "markdown",
                            "content": f"**防守止损**\n{stop_loss}"
                        }]
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [{
                            "tag": "markdown",
                            "content": (
                                f"**评分构成**\n"
                                f"Python {int(python_score)} + AI {int(ai_score)}"
                            )
                        }]
                    }
                ]
            },

            {"tag": "hr"},

            # ── 完整 AI 报告折叠展示 ──────────────────────────────────────────
            # 飞书卡片不支持原生折叠，用 markdown 块显示，超长内容已截断
            {
                "tag": "markdown",
                "content": (
                    f"**📋 完整诊断报告**\n"
                    f"{report_truncated}"
                )
            },

            # ── 底部注释 ──────────────────────────────────────────────────────
            {
                "tag": "note",
                "elements": [{
                    "tag": "plain_text",
                    "content": (
                        f"StockAI Funnel v3.0 · 三层漏斗架构 · "
                        f"本地 Ollama (qwen2.5:7b) 驱动 · "
                        f"仅供参考，不构成投资建议"
                    )
                }]
            }
        ]
    }

    payload = {"msg_type": "interactive", "card": card}
    return _post(payload)


# =============================================================================
# 今日精选汇总卡片（发多只股票的汇总表）
# =============================================================================

def send_daily_summary(
    results: List[Dict],
    session_name: str = "日常",
    total_scanned: int = 0,
) -> bool:
    """
    发送今日精选金股汇总卡片（表格 + 分级统计）。

    results 中每个 dict 需包含：
        ts_code, name, industry, total_score, grade, ai_conclusion(可选)

    卡片结构：
    ┌─────────────────────────────────────────┐
    │ 📊 StockAI 三层漏斗今日扫描报告         │
    ├─────────────────────────────────────────┤
    │ 全市场扫描 → 候选池 → AI 精选           │
    ├─────────────────────────────────────────┤
    │ 代码     名称   行业   得分  评级        │
    │ 000001   平安   银行   92   🔴 S级       │
    │ ...                                     │
    ├─────────────────────────────────────────┤
    │ S级: X只  A级: X只  总扫描: XXXX 只     │
    └─────────────────────────────────────────┘
    """
    if not results:
        # 无候选股时发简单文本通知
        return send_text(
            f"📊 {session_name} · 三层漏斗扫描完成\n"
            f"本次全市场扫描 {total_scanned:,} 只股票，暂无符合条件的精选标的（得分 > 80）。"
        )

    # ── 分级统计 ──────────────────────────────────────────────────────────────
    s_list = [r for r in results if r.get("total_score", 0) >= 85]
    a_list = [r for r in results if 80 <= r.get("total_score", 0) < 85]

    # ── 构建股票列表 Markdown 表格 ────────────────────────────────────────────
    # 飞书 Markdown 支持标准 GitHub 表格语法
    table_lines = [
        "**股票代码** | **名称** | **行业** | **得分** | **评级**",
        ":--- | :--- | :--- | :---: | :---"
    ]
    for r in sorted(results, key=lambda x: x.get("total_score", 0), reverse=True):
        score  = r.get("total_score", 0)
        grade  = _score_to_grade_emoji(score)
        table_lines.append(
            f"{r['ts_code']} | {r['name']} | {r.get('industry','—')} | "
            f"**{int(score)}** | {grade}"
        )

    table_md = "\n".join(table_lines)

    # ── 决定卡片颜色（有 S 级用红色，仅 A 级用橙色）─────────────────────────
    header_color = "red" if s_list else ("orange" if a_list else "blue")

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"🔥 今日精选金股 · {session_name}"
            },
            "subtitle": {
                "tag": "plain_text",
                "content": (
                    f"全市场 {total_scanned:,} 只 → Python 过滤 → AI 精选 {len(results)} 只  "
                    f"|  {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                )
            },
            "template": header_color
        },
        "elements": [

            # ── 漏斗统计数字 ──────────────────────────────────────────────────
            {
                "tag": "column_set",
                "flex_mode": "none",
                "background_style": "grey",
                "columns": [
                    {
                        "tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown",
                                      "content": f"**全市场扫描**\n{total_scanned:,} 只"}]
                    },
                    {
                        "tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown",
                                      "content": f"**精选输出**\n{len(results)} 只（>80分）"}]
                    },
                    {
                        "tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown",
                                      "content": f"**S级 (≥85)**\n🔴 {len(s_list)} 只"}]
                    },
                    {
                        "tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown",
                                      "content": f"**A级 (80-84)**\n🟠 {len(a_list)} 只"}]
                    },
                ]
            },

            {"tag": "hr"},

            # ── 股票明细表格 ──────────────────────────────────────────────────
            {
                "tag": "markdown",
                "content": table_md
            },

            {"tag": "hr"},

            # ── 底部注释 ──────────────────────────────────────────────────────
            {
                "tag": "note",
                "elements": [{
                    "tag": "plain_text",
                    "content": (
                        "StockAI Funnel v3.0 · 三层漏斗: Python硬过滤 → Ollama AI分析(score>80) → 飞书推送 "
                        "· 仅供参考，不构成投资建议"
                    )
                }]
            }
        ]
    }

    return _post({"msg_type": "interactive", "card": card})


# =============================================================================
# 兼容旧接口（send_scan_summary / send_ai_report / send_text）
# =============================================================================

def send_text(text: str) -> bool:
    """发送纯文本（兼容旧调用 / 错误通知用）"""
    payload = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": "StockAI Funnel 通知",
                    "content": [[{"tag": "text", "text": text}]]
                }
            }
        }
    }
    return _post(payload)


def send_scan_summary(candidates: list, session_name: str = "") -> bool:
    """兼容 scheduler.py 旧调用：转发至 send_daily_summary"""
    results = []
    for c in candidates:
        results.append({
            "ts_code":     c.get("ts_code", ""),
            "name":        c.get("name", ""),
            "industry":    c.get("industry", ""),
            "total_score": c.get("score", 0),
            "grade":       c.get("grade", "C"),
        })
    return send_daily_summary(results, session_name=session_name)


def send_ai_report(ts_code: str, name: str, grade: str, report_md: str) -> bool:
    """兼容旧调用：用最小信息组装 send_stock_report"""
    score_map = {"S": 90, "A": 82, "B": 65, "C": 40}
    total = score_map.get(grade, 70)
    return send_stock_report(
        ts_code=ts_code,
        name=name,
        total_score=total,
        python_score=total * 0.7,
        ai_score=total * 0.3,
        report_md=report_md,
    )


def send_markdown_card(title: str, content: str, grade: str = "") -> bool:
    """兼容旧调用：简单卡片"""
    color_map = {"S": "red", "A": "orange", "B": "yellow", "C": "grey"}
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color_map.get(grade, "blue")
        },
        "elements": [
            {"tag": "markdown", "content": content[:4000]},
            {"tag": "note", "elements": [{"tag": "plain_text",
                                          "content": f"StockAI Funnel · {datetime.now().strftime('%Y-%m-%d %H:%M')}"}]}
        ]
    }
    return _post({"msg_type": "interactive", "card": card})


# =============================================================================
# 独立测试入口
# =============================================================================
if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO,
                         format="%(asctime)s [%(levelname)s] %(message)s")

    # 测试1：单只股票报告卡片
    print("[TEST 1] 发送单只股票卡片...")
    ok1 = send_stock_report(
        ts_code="000001.SZ",
        name="平安银行",
        total_score=88,
        python_score=62,
        ai_score=26,
        report_md=(
            "# 平安银行 (000001.SZ) | 综合评级: S级\n"
            "> **一句话决策**：量价结构优秀，筹码高度集中，建议逢低建仓，止损设在前低。\n\n"
            "## 核心交易计划\n"
            "- **操作方向**：🟢 逢低建仓\n"
            "- **仓位建议**：**15%**\n"
            "- **防守止损**：¥10.20\n\n"
            "## 多维诊断分析\n"
            "| 维度 | 状态 | 核心解读 |\n"
            "|:---|:---|:---|\n"
            "| 技术形态 | 多头排列 | 均线向上发散，趋势健康 |\n"
            "| 资金量能 | 量比 1.83 | 温和放量上涨，买盘积极 |\n"
        ),
        industry="银行",
        close_price=10.99,
        pct_chg=0.73,
        session_name="早盘异动",
    )
    print(f"  单股卡片: {'成功' if ok1 else '失败'}")

    # 测试2：汇总卡片
    print("[TEST 2] 发送汇总摘要卡片...")
    ok2 = send_daily_summary(
        results=[
            {"ts_code": "000001.SZ", "name": "平安银行", "industry": "银行",
             "total_score": 88, "grade": "S"},
            {"ts_code": "600519.SH", "name": "贵州茅台", "industry": "白酒",
             "total_score": 83, "grade": "A"},
            {"ts_code": "300750.SZ", "name": "宁德时代", "industry": "新能源",
             "total_score": 81, "grade": "A"},
        ],
        session_name="尾盘信号",
        total_scanned=5207,
    )
    print(f"  汇总卡片: {'成功' if ok2 else '失败'}")

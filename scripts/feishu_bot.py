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

# ── Webhook 地址读取（从 config.json 读取）─────────────────────────────────────
try:
    from config_loader import get_config
    FEISHU_WEBHOOK = get_config("api.feishu_webhook", "")
except ImportError:
    try:
        from scripts.tokens import FEISHU_WEBHOOK
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
    trade_date: str = "",
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
                "content": f"{session_name or ''}  |  {industry}  |  {trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]} 评估"
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
                    "content": "仅供参考，不构成投资建议"
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
    session_name: str = "盘后分析",
    total_scanned: int = 5000,
    trade_date: str = "",
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
                    f"|  {trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]} 评估" if trade_date else
                    f"全市场 {total_scanned:,} 只 → Python 过滤 → AI 精选 {len(results)} 只"
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
                        "仅供参考，不构成投资建议"
                    )
                }]
            }
        ]
    }

    return _post({"msg_type": "interactive", "card": card})


# =============================================================================
# 陈明专属：精简版推送（30秒可读完）
# =============================================================================

def send_daily_brief(
    market_mode: str = "防守",
    max_position: float = 0.3,
    main_industries: list = None,
    backup_industries: list = None,
    portfolio_status: list = None,
    selected_stocks: list = None,
    trade_date: str = "",
    attachment_url: str = "",
    data_label: str = "",
) -> bool:
    """
    发送精简版"今日操作简报"，专为陈明设计，30秒可读完。
    
    参数：
        market_mode: 市场环境（进攻/防守/空仓）
        max_position: 仓位上限（0.3 = 30%）
        main_industries: 主线行业列表
        backup_industries: 备选行业列表
        portfolio_status: 持仓体检结果列表
        selected_stocks: 今日精选股票列表
        trade_date: 交易日期
        attachment_url: 完整报告附件链接
        data_label: 数据日期标注（如"📅 数据基于：2026-06-07（昨日收盘）"）
    """
    main_industries = main_industries or []
    backup_industries = backup_industries or []
    portfolio_status = portfolio_status or []
    selected_stocks = selected_stocks or []
    
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if trade_date else ""
    
    # 构建简报内容
    content_lines = []
    
    # 标题
    content_lines.append(f"📈 **今日操作简报** | {date_str}")
    content_lines.append("")
    
    # 数据日期标注
    if data_label:
        content_lines.append(data_label)
        content_lines.append("")
    
    # 市场环境
    content_lines.append(f"🔍 **市场环境**：{market_mode}模式，仓位控制在{int(max_position*100)}%以内")
    
    # 主线行业
    if main_industries:
        content_lines.append(f"📊 **主线行业**：{'、'.join(main_industries)}")
    if backup_industries:
        content_lines.append(f"   **备选**：{'、'.join(backup_industries)}")
    content_lines.append("")
    
    # 持仓体检
    if portfolio_status:
        content_lines.append("📌 **您的持仓**：")
        for pos in portfolio_status:
            ts_code = pos.get("ts_code", "")[:6]
            name = pos.get("name", "")
            action = pos.get("action", "持有")
            reason = pos.get("reason", "")
            stop_loss = pos.get("stop_loss", "")
            
            content_lines.append(f"✅ **{ts_code} {name}**：{action}")
            if stop_loss:
                content_lines.append(f"   {stop_loss}")
            if reason:
                content_lines.append(f"   理由：{reason}")
        content_lines.append("")
    
    # 今日精选
    if selected_stocks:
        content_lines.append("🔥 **今日精选**：")
        for stock in selected_stocks:
            ts_code = stock.get("ts_code", "")[:6]
            name = stock.get("name", "")
            score = stock.get("score", 0)
            suggestion = stock.get("suggestion", "")
            
            content_lines.append(f"🟢 **{ts_code} {name}**（{score}分）→ {suggestion}")
        content_lines.append("")
    
    # 底部提示
    content_lines.append("⏰ 完整报告见附件")
    content_lines.append("⚠️ 量化系统生成，不构成投资建议")
    
    # 转为飞书卡片
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"📈 今日操作简报"},
            "subtitle": {"tag": "plain_text", "content": date_str},
            "template": "blue"
        },
        "elements": [
            {
                "tag": "markdown",
                "content": "\n".join(content_lines)
            },
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": "仅供参考，不构成投资建议"}]
            }
        ]
    }
    
    return _post({"msg_type": "interactive", "card": card})


def send_intraday_alert(
    ts_code: str,
    name: str,
    current_price: float,
    pct_chg: float,
    trigger_type: str,
    trigger_value: str,
    suggestion: str = "立即清仓",
) -> bool:
    """
    发送盘中紧急告警（增强版，添加关键词通过飞书安全检测）。
    
    参数：
        ts_code: 股票代码
        name: 股票名称
        current_price: 当前价格
        pct_chg: 涨跌幅
        trigger_type: 触发类型（跌破止损/接近跌停/主力异常流出）
        trigger_value: 触发值（如止损价）
        suggestion: 操作建议
    """
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 构建内容（添加关键词）
    content_lines = []
    content_lines.append("🚨 **StockAI 盘中紧急告警通知**")
    content_lines.append("")
    content_lines.append(f"📅 告警时间：{now}")
    content_lines.append(f"📊 股票代码：{ts_code[:6]}（{name}）")
    content_lines.append("")
    content_lines.append(f"💹 **当前行情数据**：")
    content_lines.append(f"   股票价格：¥{current_price:.2f}")
    content_lines.append(f"   涨跌幅：{pct_chg:+.2f}%")
    content_lines.append("")
    content_lines.append(f"⚠️ **量化系统触发告警**：")
    content_lines.append(f"   触发类型：{trigger_type}")
    content_lines.append(f"   触发数值：{trigger_value}")
    content_lines.append("")
    content_lines.append(f"🎯 **StockAI 量化分析操作建议**：{suggestion}")
    content_lines.append("")
    content_lines.append("---")
    content_lines.append("⚠️ 本告警由 StockAI 量化分析系统自动生成，仅供参考，投资有风险")
    
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🚨 StockAI 盘中紧急告警"},
            "subtitle": {"tag": "plain_text", "content": f"{ts_code[:6]} {name} · {pct_chg:+.2f}%"},
            "template": "red"
        },
        "elements": [
            {
                "tag": "markdown",
                "content": "\n".join(content_lines)
            },
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": "StockAI 量化系统 · 盘中监控 · 投资有风险"}]
            }
        ]
    }
    
    return _post({"msg_type": "interactive", "card": card})


def send_portfolio_report(holdings: list, trade_date: str = "") -> bool:
    """
    发送独立的"持仓健康度报告"，专为陈明设计。
    
    参数：
        holdings: 持仓列表，来自 portfolio_health.check_portfolio() 返回值
        trade_date: 交易日期 (如 20260608)
    """
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if trade_date else datetime.now().strftime("%Y-%m-%d")
    
    # 构建报告内容（添加关键词以通过飞书安全检测）
    content_lines = []
    
    # 标题（添加关键词）
    content_lines.append("� **StockAI 持仓健康度量化分析报告**")
    content_lines.append(f"📅 报告日期：{date_str}")
    content_lines.append("")
    
    if not holdings:
        # 无持仓时的提示
        content_lines.append("⚠️ 当前无持仓记录，请在 StockAI 托盘菜单中添加股票持仓")
        content_lines.append("股票代码格式：600519.SH（上海）或 000001.SZ（深圳）")
    else:
        # 添加摘要
        total_count = len(holdings)
        buy_count = sum(1 for h in holdings if "持有" in h.get("suggestion", ""))
        sell_count = sum(1 for h in holdings if "减仓" in h.get("suggestion", "") or "清仓" in h.get("suggestion", ""))
        content_lines.append(f"📈 持仓概览：共 {total_count} 只股票，建议持有 {buy_count} 只，建议减仓 {sell_count} 只")
        content_lines.append("")
        
        # 逐个显示持仓健康度
        content_lines.append("### 个股详细分析")
        for holding in holdings:
            ts_code = holding.get("ts_code", "")[:6]
            name = holding.get("name", "")
            cost = holding.get("cost", 0.0)
            current_price = holding.get("current_price", 0.0)
            pnl_pct = holding.get("pnl_pct", 0.0)
            score = holding.get("score", 0)
            suggestion = holding.get("suggestion", "")
            reason = holding.get("reason", "")
            
            # 决定emoji
            if "清仓" in suggestion:
                emoji = "🔴"
            elif "减仓" in suggestion:
                emoji = "🟠"
            elif "持有" in suggestion:
                emoji = "✅"
            else:
                emoji = "🟡"
            
            # 盈亏颜色标记
            pnl_color = "green" if pnl_pct > 0 else ("red" if pnl_pct < 0 else "grey")
            
            # 完整信息（添加关键词）
            content_lines.append(f"{emoji} **{ts_code} {name}**（量化评分：{score}分）")
            content_lines.append(f"   成本价：¥{cost:.2f} | 当前价：¥{current_price:.2f} | 盈亏：<font color='{pnl_color}'>{pnl_pct:+.2f}%</font>")
            content_lines.append(f"   📌 操作建议：{suggestion}")
            content_lines.append(f"   📝 量化理由：{reason}")
            
            # 计算建议止损（简单逻辑：成本价 * 0.95）
            stop_loss = cost * 0.95
            if current_price > 0 and pnl_pct > 0:
                content_lines.append(f"   🎯 建议止损价：¥{max(stop_loss, cost):.2f}")
            
            content_lines.append("")
    
    # 底部提示（添加关键词）
    content_lines.append("---")
    content_lines.append("� 本报告由 StockAI 量化分析系统自动生成，基于多因子评分模型")
    content_lines.append("📊 数据来源：主力资金流向、筹码结构、股东户数变化等量化指标")
    content_lines.append("⚠️ 免责声明：本报告仅供参考，不构成任何投资建议，投资有风险")
    
    # 转为飞书卡片
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "� StockAI 持仓健康度量化分析"},
            "subtitle": {"tag": "plain_text", "content": f"{date_str} · {len(holdings) if holdings else 0}只持仓"},
            "template": "blue"
        },
        "elements": [
            {
                "tag": "markdown",
                "content": "\n".join(content_lines)
            },
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": "StockAI 量化系统 · 仅供参考 · 投资有风险"}]
            }
        ]
    }
    
    return _post({"msg_type": "interactive", "card": card})


def send_error_notification(error_msg: str) -> bool:
    """
    发送系统错误通知到飞书（增强版，添加关键词）
    """
    # 构建内容（添加关键词）
    content_lines = []
    content_lines.append("🚨 **StockAI 量化系统异常告警通知**")
    content_lines.append("")
    content_lines.append(f"📅 告警时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    content_lines.append("")
    content_lines.append(f"⚠️ **系统运行异常详情**：")
    content_lines.append(f"   错误类型：{error_msg}")
    content_lines.append("")
    content_lines.append("💡 **建议操作**：")
    content_lines.append("   请检查系统日志和数据库连接状态")
    content_lines.append("   如问题持续，请联系技术支持")
    content_lines.append("")
    content_lines.append("---")
    content_lines.append("📊 StockAI 量化分析系统 · 系统监控通知 · 请及时处理")
    
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🚨 StockAI 系统异常告警"},
            "template": "red"
        },
        "elements": [
            {
                "tag": "markdown",
                "content": "\n".join(content_lines)
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


def send_full_daily_briefing(
    market_mode: str = "防守",
    max_position: float = 0.3,
    main_industries: list = None,
    backup_industries: list = None,
    market_rise_ratio: float = 0.55,
    portfolio_status: list = None,
    selected_stocks: list = None,
    trade_date: str = ""
) -> bool:
    """
    恢复旧版完整格式推送：飞书推送更新 · 每日精选
    
    参数：
        market_mode: 市场环境（进攻/防守/空仓）
        max_position: 仓位上限
        main_industries: 主线行业列表
        backup_industries: 备选行业列表
        market_rise_ratio: 上涨占比
        portfolio_status: 持仓体检结果（可选）
        selected_stocks: 精选股票列表，每只股票包含：
            - ts_code: 股票代码
            - name: 股票名称
            - total_score: 综合得分
            - grade: 评级
            - main_money: 主力资金流向（字符串或数值）
            - hsgt_5d: 近5日北向资金
            - holder_chg: 股东户数变化（%）
            - block_premium: 大宗交易情况（字符串）
            - margin_ratio: 融资余额占比（%）
            - ai_conclusion: AI核心结论
            - buy_range: 买入区间
            - position: 仓位建议
            - stop_loss: 止损价
        trade_date: 交易日期
    """
    main_industries = main_industries or []
    backup_industries = backup_industries or []
    portfolio_status = portfolio_status or []
    selected_stocks = selected_stocks or []
    
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if trade_date else datetime.now().strftime("%Y-%m-%d")
    
    content_lines = []
    
    # 标题
    content_lines.append("## 🔥 飞书推送更新 · 每日精选\n")
    
    # 整体分析
    content_lines.append("### 📊 整体分析")
    content_lines.append(f"- **市场环境**：{market_mode}模式，仓位控制在{int(max_position*100)}%以内")
    
    if main_industries:
        content_lines.append(f"- **主线行业**：{ '、'.join(main_industries) }")
    if backup_industries:
        content_lines.append(f"- **备选行业**：{ '、'.join(backup_industries) }")
    
    content_lines.append(f"- **市场温度**：上涨占比{market_rise_ratio:.1%}\n")
    
    # 您的持仓
    if portfolio_status:
        content_lines.append("### 📌 您的持仓")
        for pos in portfolio_status:
            ts_code = pos.get("ts_code", "")[:6]
            name = pos.get("name", "")
            action = pos.get("action", "持有")
            reason = pos.get("reason", "")
            content_lines.append(f"- **{ts_code} {name}**：{action}")
            if reason:
                content_lines.append(f"  理由：{reason}")
        content_lines.append("")
    
    # 精选股票
    if selected_stocks:
        content_lines.append("### 🔍 精选股票")
        for stock in selected_stocks:
            ts_code = stock.get("ts_code", "")[:6]
            name = stock.get("name", "")
            total_score = stock.get("total_score", 0)
            grade = stock.get("grade", "C")
            
            content_lines.append(f"#### **{name}（{ts_code}）**")
            content_lines.append(f"- **综合得分**：{total_score}分 · **评级**：{grade}")
            
            # 主力资金
            main_money = stock.get("main_money", "N/A")
            if isinstance(main_money, (int, float)):
                if main_money > 0:
                    content_lines.append(f"- **主力资金**：净流入{main_money:.0f}万")
                else:
                    content_lines.append(f"- **主力资金**：净流出{abs(main_money):.0f}万")
            else:
                content_lines.append(f"- **主力资金**：{main_money}")
            
            # 北向资金
            hsgt_5d = stock.get("hsgt_5d", "N/A")
            if isinstance(hsgt_5d, (int, float)):
                if hsgt_5d > 0:
                    content_lines.append(f"- **北向资金**：近5日净流入{hsgt_5d:.0f}万")
                else:
                    content_lines.append(f"- **北向资金**：近5日净流出{abs(hsgt_5d):.0f}万")
            else:
                content_lines.append(f"- **北向资金**：{hsgt_5d}")
            
            # 股东户数
            holder_chg = stock.get("holder_chg", "N/A")
            if isinstance(holder_chg, (int, float)):
                if holder_chg < 0:
                    content_lines.append(f"- **股东户数**：较上期下降{abs(holder_chg):.1%}")
                else:
                    content_lines.append(f"- **股东户数**：较上期增长{holder_chg:.1%}")
            else:
                content_lines.append(f"- **股东户数**：{holder_chg}")
            
            # 大宗交易
            block_premium = stock.get("block_premium", "无")
            content_lines.append(f"- **大宗交易**：近期{'有' if block_premium and '有' in block_premium else '无'}溢价成交")
            
            # 融资余额
            margin_ratio = stock.get("margin_ratio", "N/A")
            if isinstance(margin_ratio, (int, float)):
                content_lines.append(f"- **融资余额**：占流通市值{margin_ratio:.2%}")
            else:
                content_lines.append(f"- **融资余额**：{margin_ratio}")
            
            # AI核心结论
            ai_conclusion = stock.get("ai_conclusion", "")
            if ai_conclusion:
                content_lines.append(f"- **AI核心结论**：{ai_conclusion}")
            
            # 交易计划
            buy_range = stock.get("buy_range", "")
            position = stock.get("position", "")
            stop_loss = stock.get("stop_loss", "")
            if buy_range or position or stop_loss:
                content_lines.append("- **交易计划**：")
                if buy_range:
                    content_lines.append(f"  买入区间：{buy_range}")
                if position:
                    content_lines.append(f"  仓位建议：{position}")
                if stop_loss:
                    content_lines.append(f"  止损价：{stop_loss}")
            
            content_lines.append("")  # 分隔
    
    # 底部提示
    content_lines.append("---")
    content_lines.append("⚠️ 量化系统生成，不构成投资建议")
    
    # 构建飞书卡片
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🔥 飞书推送更新 · 每日精选"},
            "subtitle": {"tag": "plain_text", "content": date_str},
            "template": "red" if selected_stocks else "blue"
        },
        "elements": [
            {
                "tag": "markdown",
                "content": "\n".join(content_lines)[:8000]
            },
            {
                "tag": "note",
                "elements": [{"tag": "plain_text",
                              "content": f"StockAI Funnel · {datetime.now().strftime('%Y-%m-%d %H:%M')}"}]
            }
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

    # 测试3：完整格式每日精选
    print("[TEST 3] 发送完整格式每日精选...")
    ok3 = send_full_daily_briefing(
        market_mode="防守",
        max_position=0.3,
        main_industries=["白酒", "电力"],
        backup_industries=["光伏", "银行"],
        market_rise_ratio=0.58,
        portfolio_status=[
            {"ts_code": "600519.SH", "name": "贵州茅台", "action": "继续持有", "reason": "主力净流入+2300万"},
        ],
        selected_stocks=[
            {
                "ts_code": "000858.SZ",
                "name": "五粮液",
                "total_score": 33,
                "grade": "强信号",
                "main_money": 2100,
                "hsgt_5d": 3500,
                "holder_chg": -0.085,
                "block_premium": "有",
                "margin_ratio": 0.045,
                "ai_conclusion": "量价结构优秀，筹码高度集中，建议逢低建仓",
                "buy_range": "¥145-148",
                "position": "轻仓介入，仓位10-15%",
                "stop_loss": "¥142"
            }
        ],
        trade_date="20260608"
    )
    print(f"  完整格式每日精选: {'成功' if ok3 else '失败'}")

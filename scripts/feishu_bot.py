# -*- coding: utf-8 -*-
"""
feishu_bot.py —— StockAI v4.0 飞书机器人推送模块 (全新交互卡片版)
=====================================================================
核心功能：
  1. 市场全局汇总卡片（涨跌家数、成交额、涨停统计、5日线占比、主线板块）
  2. 板块汇总卡片（涨停家数、连板、信号统计、资金流向）
  3. 精选金股卡片（完整评分体系、交易方案、风险提示、AI解读）

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

try:
    from config_loader import get_config
    FEISHU_WEBHOOK = get_config("api.feishu_webhook", "")
except ImportError:
    try:
        from scripts.tokens import FEISHU_WEBHOOK
    except ImportError:
        FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")

TIMEOUT = 12

# 色彩规范
COLORS = {
    "strong": "#f53f3f",      # 亮红 - 强信号/上涨/风险警示
    "medium": "#ffaa00",      # 亮黄 - 中信号/普通提示
    "filter": "#00b42a",      # 青绿 - 下跌/过滤标签
    "text": "#e5e6eb",        # 浅灰 - 常规内容
    "bg": "rgba(40,50,70,0.6)"# 半透深色 - 标签背景
}


def _post(payload: dict) -> bool:
    if not FEISHU_WEBHOOK:
        log.warning("FEISHU_WEBHOOK 未配置，跳过推送")
        return False
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", -1) == 0 or data.get("StatusCode", 1) == 0:
            log.info("飞书推送成功")
            return True
        else:
            log.warning("飞书返回异常: %s", data)
            return False
    except Exception as e:
        log.error("飞书推送失败: %s", e)
        return False


def _score_to_color(score: float) -> str:
    if score >= 85:
        return "red"
    elif score >= 80:
        return "orange"
    else:
        return "blue"


def _score_to_grade_emoji(score: float) -> str:
    if score >= 85:
        return "🔴 S级 · 强烈关注"
    elif score >= 80:
        return "🟠 A级 · 纳入观察"
    elif score >= 60:
        return "🟡 B级 · 参考"
    else:
        return "⚪ C级 · 观望"


def _extract_ai_conclusion(ai_text: str) -> str:
    import re
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
            conclusion = re.sub(r"[\*\`\[\]]+", "", conclusion).strip()
            return conclusion[:100]
    for line in ai_text.splitlines():
        line = re.sub(r"^[#\>\-\*\s]+", "", line).strip()
        if len(line) > 10:
            return line[:80] + ("…" if len(line) > 80 else "")
    return "AI 分析已完成，请查看完整报告"


def _extract_operation(ai_text: str) -> str:
    import re
    m = re.search(r"\*\*操作方向\*\*[：:]\s*(.+?)(?:\n|$)", ai_text)
    if m:
        return re.sub(r"[\*\[\]]+", "", m.group(1)).strip()[:30]
    if any(w in ai_text for w in ["逢低建仓", "可以介入", "买入"]):
        return "🟢 逢低建仓"
    if any(w in ai_text for w in ["建议规避", "暂不介入", "卖出"]):
        return "🔴 规避"
    return "🟡 观望"


def _extract_stop_loss(ai_text: str) -> str:
    import re
    m = re.search(r"防守止损[：:][^\¥]*¥\s*([\d\.]+)", ai_text)
    if m:
        return "¥" + m.group(1)
    m2 = re.search(r"止损[：:位至][^\d]*([\d\.]+)", ai_text)
    if m2:
        return "¥" + m2.group(1)
    return "见报告"


# =============================================================================
# 新增：市场全局汇总卡片
# =============================================================================
def send_market_summary_card(
    trade_date: str = "",
    market_mode: str = "防守",
    sh_index: float = 0.0,
    sh_pct: float = 0.0,
    up_count: int = 0,
    down_count: int = 0,
    total_stocks: int = 0,
    turnover: float = 0.0,
    limit_up_count: int = 0,
    limit_down_count: int = 0,
    ma5_ratio: float = 0.0,
    main_sectors: list = None,
    backup_sectors: list = None,
) -> bool:
    """
    发送市场全局汇总卡片。
    
    参数：
        trade_date: 交易日期
        market_mode: 市场模式（进攻/防守/空仓）
        sh_index: 上证指数点数
        sh_pct: 上证指数涨跌幅
        up_count: 上涨家数
        down_count: 下跌家数
        total_stocks: 总股票数
        turnover: 成交额（亿元）
        limit_up_count: 涨停家数
        limit_down_count: 跌停家数
        ma5_ratio: 5日线占比
        main_sectors: 主线板块列表
        backup_sectors: 备选板块列表
    """
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if trade_date else datetime.now().strftime("%Y-%m-%d")
    
    up_ratio = up_count / total_stocks if total_stocks > 0 else 0.5
    pct_color = "red" if sh_pct > 0 else ("green" if sh_pct < 0 else "grey")
    
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📊 市场全局汇总"},
            "subtitle": {"tag": "plain_text", "content": date_str},
            "template": "blue"
        },
        "elements": [
            {
                "tag": "column_set",
                "flex_mode": "none",
                "background_style": "grey",
                "columns": [
                    {
                        "tag": "column", "width": "weighted", "weight": 2,
                        "elements": [{"tag": "markdown",
                            "content": f"**上证指数**\n{sh_index:.2f}\n<font color='{pct_color}'>{sh_pct:+.2f}%</font>"}]
                    },
                    {
                        "tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown",
                            "content": f"**市场模式**\n{market_mode}模式"}]
                    },
                    {
                        "tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown",
                            "content": f"**上涨占比**\n{up_ratio:.1%}"}]
                    }
                ]
            },
            {"tag": "hr"},
            {
                "tag": "column_set",
                "flex_mode": "none",
                "columns": [
                    {
                        "tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown",
                            "content": f"**涨跌家数**\n↑ {up_count} | ↓ {down_count}"}]
                    },
                    {
                        "tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown",
                            "content": f"**成交额**\n{turnover:.0f} 亿"}]
                    },
                    {
                        "tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown",
                            "content": f"**涨停/跌停**\n{limit_up_count} / {limit_down_count}"}]
                    },
                    {
                        "tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown",
                            "content": f"**5日线占比**\n{ma5_ratio:.1%}"}]
                    }
                ]
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": (
                    f"**🎯 主线板块**：{'、'.join(main_sectors) if main_sectors else '暂无'}\n"
                    f"**📌 备选板块**：{'、'.join(backup_sectors) if backup_sectors else '暂无'}"
                )
            },
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": "StockAI 量化系统 · 仅供参考"}]
            }
        ]
    }
    return _post({"msg_type": "interactive", "card": card})


# =============================================================================
# 新增：板块汇总卡片
# =============================================================================
def send_sector_summary_card(
    sectors: list,
    trade_date: str = ""
) -> bool:
    """
    发送板块汇总卡片。
    
    sectors 列表中每个元素包含：
        name: 板块名称
        limit_up_count: 涨停家数
        consecutive_count: 连板家数
        signal_count: 信号数
        money_flow: 资金流向（亿元）
        trend: 趋势描述
    """
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if trade_date else datetime.now().strftime("%Y-%m-%d")
    
    table_lines = ["**板块名称** | **涨停数** | **连板** | **信号数** | **资金流向**",
                   ":--- | :---: | :---: | :---: | :---"]
    for sector in sectors:
        mf = sector.get("money_flow", 0)
        mf_str = f"流入{mf:.1f}亿" if mf > 0 else f"流出{abs(mf):.1f}亿" if mf < 0 else "持平"
        table_lines.append(
            f"{sector.get('name', '')} | {sector.get('limit_up_count', 0)} | "
            f"{sector.get('consecutive_count', 0)} | {sector.get('signal_count', 0)} | {mf_str}"
        )
    
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📈 板块热力图"},
            "subtitle": {"tag": "plain_text", "content": date_str},
            "template": "purple"
        },
        "elements": [
            {
                "tag": "markdown",
                "content": "\n".join(table_lines)
            },
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": "StockAI 量化系统 · 仅供参考"}]
            }
        ]
    }
    return _post({"msg_type": "interactive", "card": card})


# =============================================================================
# 重构：精选金股卡片（完整格式）
# =============================================================================
def send_stock_report_v2(
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
    market_mode: str = "防守",
    # 新增字段
    score_details: dict = None,
    buy_range: str = "",
    stop_loss_main: str = "",
    stop_loss_struct: str = "",
    final_stop_loss: str = "",
    position_defense: str = "",
    position_attack: str = "",
    logic_basis: str = "",
    risks: list = None,
    ai_analysis: dict = None,
) -> bool:
    """
    发送全新格式的精选金股卡片。
    """
    conclusion = _extract_ai_conclusion(report_md)
    operation = _extract_operation(report_md)
    
    pct_str = f"{pct_chg:+.2f}%" if pct_chg != 0 else "N/A"
    pct_color = "red" if pct_chg > 0 else ("green" if pct_chg < 0 else "grey")
    
    header_color = _score_to_color(total_score)
    grade_text = _score_to_grade_emoji(total_score)
    
    filled = min(int(total_score // 4), 10)
    bar = "█" * filled + "░" * (10 - filled)
    
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if trade_date else datetime.now().strftime("%Y-%m-%d")
    
    score_details = score_details or {}
    risks = risks or []
    ai_analysis = ai_analysis or {}
    
    content_lines = []
    
    # 一、核心加分项
    content_lines.append("## 一、核心评分明细")
    content_lines.append("")
    
    add_items = []
    deduct_items = []
    for key, value in score_details.items():
        if value > 0:
            add_items.append(f"- **{key}**：+{value} 分")
        elif value < 0:
            deduct_items.append(f"- **{key}**：{value} 分")
    
    if add_items:
        content_lines.append("### 🟢 加分项")
        content_lines.extend(add_items)
    
    if deduct_items:
        content_lines.append("")
        content_lines.append("### 🔴 扣分项")
        content_lines.extend(deduct_items)
    
    content_lines.append("")
    content_lines.append(f"**总分计算**：{python_score:.0f} + {ai_score:.0f} = **{total_score:.0f} 分**")
    
    # 二、大盘与行业模式
    content_lines.append("")
    content_lines.append("## 二、大盘与行业模式")
    content_lines.append(f"- **当前大盘模式**：{market_mode}模式")
    content_lines.append(f"- **所属行业层级**：{industry}（普通行业）")
    
    # 三、交易方案
    content_lines.append("")
    content_lines.append("## 三、交易方案")
    content_lines.append(f"- **操作方向**：{operation}")
    content_lines.append(f"- **推荐仓位**：防守模式 {position_defense} | 进攻模式 {position_attack}")
    content_lines.append(f"- **铁律限制**：单股持仓上限 20%，禁止超限")
    
    if buy_range:
        content_lines.append(f"- **买入参考区间**：{buy_range}")
    
    content_lines.append("")
    content_lines.append("### 🛡️ 双止损规则")
    if stop_loss_main:
        content_lines.append(f"- 主止损：{stop_loss_main}")
    if stop_loss_struct:
        content_lines.append(f"- 结构止损：{stop_loss_struct}")
    if final_stop_loss:
        content_lines.append(f"- **最终执行止损**：<font color='red'>{final_stop_loss}</font>")
    
    # 四、逻辑依据
    content_lines.append("")
    content_lines.append("## 四、逻辑依据")
    content_lines.append(logic_basis if logic_basis else "待补充")
    
    # 五、重点风险提示
    content_lines.append("")
    content_lines.append("## 五、重点风险提示")
    for risk in risks:
        content_lines.append(f"- <font color='red'>{risk}</font>")
    
    # 六、后续跟踪计划
    content_lines.append("")
    content_lines.append("## 六、后续跟踪计划")
    content_lines.append("- **核心观察指标**：成交量是否持续放大、主力资金是否延续流入")
    content_lines.append("- **动态调仓策略**：")
    content_lines.append("  - 放量上涨：持有并观察")
    content_lines.append(f"  - 跌破止损价 {final_stop_loss}：立即止盈/止损离场")
    content_lines.append("  - 连续2日主力净流出：减仓观望")
    
    # 七、AI专项解读
    content_lines.append("")
    content_lines.append("## 七、AI专项解读")
    if ai_analysis:
        content_lines.append("| 评估维度 | 得分 | 解读 |")
        content_lines.append("|:---|:---:|:---|")
        for dim, data in ai_analysis.items():
            content_lines.append(f"| {dim} | {data.get('score', '-')} | {data.get('comment', '-')} |")
        content_lines.append("")
        content_lines.append(f"**AI一句话结论**：{conclusion}")
    
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text",
                "content": f"{'🔥' if total_score >= 85 else '📈'} 今日精选金股 · {ts_code} {name}"},
            "subtitle": {"tag": "plain_text",
                "content": f"{session_name or ''} | {industry} | {date_str} 评估"},
            "template": header_color
        },
        "elements": [
            {
                "tag": "column_set",
                "flex_mode": "none",
                "background_style": "grey",
                "columns": [
                    {
                        "tag": "column", "width": "weighted", "weight": 2,
                        "elements": [{"tag": "markdown",
                            "content": f"**综合得分**\n**{int(total_score)}** 分  {bar}\n{grade_text}"}]
                    },
                    {
                        "tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown",
                            "content": f"**收盘价**\n¥{close_price:.2f}\n<font color='{pct_color}'>{pct_str}</font>"}]
                    }
                ]
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": "\n".join(content_lines)[:8000]
            },
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": "仅供参考，不构成投资建议"}]
            }
        ]
    }
    return _post({"msg_type": "interactive", "card": card})


# =============================================================================
# 兼容旧接口
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
    """兼容旧接口，转发到新格式"""
    return send_stock_report_v2(
        ts_code=ts_code,
        name=name,
        total_score=total_score,
        python_score=python_score,
        ai_score=ai_score,
        report_md=report_md,
        industry=industry,
        close_price=close_price,
        pct_chg=pct_chg,
        session_name=session_name,
        trade_date=trade_date,
    )


def send_daily_summary(
    results: List[Dict],
    session_name: str = "盘后分析",
    total_scanned: int = 5000,
    trade_date: str = "",
) -> bool:
    """发送今日精选汇总卡片"""
    if not results:
        return send_text(
            f"📊 {session_name} · 三层漏斗扫描完成\n"
            f"本次全市场扫描 {total_scanned:,} 只股票，暂无符合条件的精选标的。"
        )
    
    s_list = [r for r in results if r.get("total_score", 0) >= 85]
    a_list = [r for r in results if 80 <= r.get("total_score", 0) < 85]
    
    table_lines = [
        "**股票代码** | **名称** | **行业** | **得分** | **评级**",
        ":--- | :--- | :--- | :---: | :---"
    ]
    for r in sorted(results, key=lambda x: x.get("total_score", 0), reverse=True):
        score = r.get("total_score", 0)
        grade = _score_to_grade_emoji(score)
        table_lines.append(
            f"{r['ts_code']} | {r['name']} | {r.get('industry','—')} | "
            f"**{int(score)}** | {grade}"
        )
    
    table_md = "\n".join(table_lines)
    header_color = "red" if s_list else ("orange" if a_list else "blue")
    
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if trade_date else ""
    
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"🔥 今日精选金股 · {session_name}"},
            "subtitle": {"tag": "plain_text",
                "content": f"全市场 {total_scanned:,} 只 → Python 过滤 → AI 精选 {len(results)} 只 | {date_str}" if date_str else
                f"全市场 {total_scanned:,} 只 → Python 过滤 → AI 精选 {len(results)} 只"},
            "template": header_color
        },
        "elements": [
            {
                "tag": "column_set",
                "flex_mode": "none",
                "background_style": "grey",
                "columns": [
                    {"tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown", "content": f"**全市场扫描**\n{total_scanned:,} 只"}]},
                    {"tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown", "content": f"**精选输出**\n{len(results)} 只"}]},
                    {"tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown", "content": f"**S级 (≥85)**\n🔴 {len(s_list)} 只"}]},
                    {"tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown", "content": f"**A级 (80-84)**\n🟠 {len(a_list)} 只"}]},
                ]
            },
            {"tag": "hr"},
            {"tag": "markdown", "content": table_md},
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": "仅供参考，不构成投资建议"}]
            }
        ]
    }
    return _post({"msg_type": "interactive", "card": card})


def send_text(text: str) -> bool:
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
    results = []
    for c in candidates:
        results.append({
            "ts_code": c.get("ts_code", ""),
            "name": c.get("name", ""),
            "industry": c.get("industry", ""),
            "total_score": c.get("score", 0),
            "grade": c.get("grade", "C"),
        })
    return send_daily_summary(results, session_name=session_name)


def send_ai_report(ts_code: str, name: str, grade: str, report_md: str) -> bool:
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
    main_industries = main_industries or []
    backup_industries = backup_industries or []
    portfolio_status = portfolio_status or []
    selected_stocks = selected_stocks or []
    
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if trade_date else ""
    
    content_lines = []
    content_lines.append(f"📈 **今日操作简报** | {date_str}")
    content_lines.append("")
    
    if data_label:
        content_lines.append(data_label)
        content_lines.append("")
    
    content_lines.append(f"🔍 **市场环境**：{market_mode}模式，仓位控制在{int(max_position*100)}%以内")
    
    if main_industries:
        content_lines.append(f"📊 **主线行业**：{'、'.join(main_industries)}")
    if backup_industries:
        content_lines.append(f"   **备选**：{'、'.join(backup_industries)}")
    content_lines.append("")
    
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
    
    if selected_stocks:
        content_lines.append("🔥 **今日精选**：")
        for stock in selected_stocks:
            ts_code = stock.get("ts_code", "")[:6]
            name = stock.get("name", "")
            score = stock.get("score", 0)
            suggestion = stock.get("suggestion", "")
            
            content_lines.append(f"🟢 **{ts_code} {name}**（{score}分）→ {suggestion}")
        content_lines.append("")
    
    content_lines.append("⏰ 完整报告见附件")
    content_lines.append("⚠️ 量化系统生成，不构成投资建议")
    
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"📈 今日操作简报"},
            "subtitle": {"tag": "plain_text", "content": date_str},
            "template": "blue"
        },
        "elements": [
            {"tag": "markdown", "content": "\n".join(content_lines)},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "仅供参考，不构成投资建议"}]}
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
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
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
            {"tag": "markdown", "content": "\n".join(content_lines)},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "StockAI 量化系统 · 盘中监控 · 投资有风险"}]}
        ]
    }
    return _post({"msg_type": "interactive", "card": card})


def send_portfolio_report(holdings: list, trade_date: str = "") -> bool:
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if trade_date else datetime.now().strftime("%Y-%m-%d")
    
    content_lines = []
    content_lines.append("📊 **StockAI 持仓健康度量化分析报告**")
    content_lines.append(f"📅 报告日期：{date_str}")
    content_lines.append("")
    
    if not holdings:
        content_lines.append("⚠️ 当前无持仓记录，请在 StockAI 托盘菜单中添加股票持仓")
        content_lines.append("股票代码格式：600519.SH（上海）或 000001.SZ（深圳）")
    else:
        total_count = len(holdings)
        buy_count = sum(1 for h in holdings if "持有" in h.get("suggestion", ""))
        sell_count = sum(1 for h in holdings if "减仓" in h.get("suggestion", "") or "清仓" in h.get("suggestion", ""))
        content_lines.append(f"📈 持仓概览：共 {total_count} 只股票，建议持有 {buy_count} 只，建议减仓 {sell_count} 只")
        content_lines.append("")
        
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
            
            emoji = "🔴" if "清仓" in suggestion else ("🟠" if "减仓" in suggestion else ("✅" if "持有" in suggestion else "🟡"))
            pnl_color = "green" if pnl_pct > 0 else ("red" if pnl_pct < 0 else "grey")
            
            content_lines.append(f"{emoji} **{ts_code} {name}**（量化评分：{score}分）")
            content_lines.append(f"   成本价：¥{cost:.2f} | 当前价：¥{current_price:.2f} | 盈亏：<font color='{pnl_color}'>{pnl_pct:+.2f}%</font>")
            content_lines.append(f"   📌 操作建议：{suggestion}")
            content_lines.append(f"   📝 量化理由：{reason}")
            
            stop_loss = cost * 0.95
            if current_price > 0 and pnl_pct > 0:
                content_lines.append(f"   🎯 建议止损价：¥{max(stop_loss, cost):.2f}")
            
            content_lines.append("")
    
    content_lines.append("---")
    content_lines.append("📊 本报告由 StockAI 量化分析系统自动生成")
    content_lines.append("⚠️ 免责声明：本报告仅供参考，不构成任何投资建议")
    
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📊 StockAI 持仓健康度量化分析"},
            "subtitle": {"tag": "plain_text", "content": f"{date_str} · {len(holdings) if holdings else 0}只持仓"},
            "template": "blue"
        },
        "elements": [
            {"tag": "markdown", "content": "\n".join(content_lines)},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "StockAI 量化系统 · 仅供参考 · 投资有风险"}]}
        ]
    }
    return _post({"msg_type": "interactive", "card": card})


def send_error_notification(error_msg: str) -> bool:
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
    content_lines.append("📊 StockAI 量化分析系统 · 系统监控通知")
    
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🚨 StockAI 系统异常告警"},
            "template": "red"
        },
        "elements": [{"tag": "markdown", "content": "\n".join(content_lines)}]
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
    main_industries = main_industries or []
    backup_industries = backup_industries or []
    portfolio_status = portfolio_status or []
    selected_stocks = selected_stocks or []
    
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if trade_date else datetime.now().strftime("%Y-%m-%d")
    
    content_lines = []
    content_lines.append("## 🔥 飞书推送更新 · 每日精选\n")
    
    content_lines.append("### 📊 整体分析")
    content_lines.append(f"- **市场环境**：{market_mode}模式，仓位控制在{int(max_position*100)}%以内")
    
    if main_industries:
        content_lines.append(f"- **主线行业**：{ '、'.join(main_industries) }")
    if backup_industries:
        content_lines.append(f"- **备选行业**：{ '、'.join(backup_industries) }")
    
    content_lines.append(f"- **市场温度**：上涨占比{market_rise_ratio:.1%}\n")
    
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
    
    if selected_stocks:
        content_lines.append("### 🔍 精选股票")
        for stock in selected_stocks:
            ts_code = stock.get("ts_code", "")[:6]
            name = stock.get("name", "")
            total_score = stock.get("total_score", 0)
            grade = stock.get("grade", "C")
            
            content_lines.append(f"#### **{name}（{ts_code}）**")
            content_lines.append(f"- **综合得分**：{total_score}分 · **评级**：{grade}")
            
            main_money = stock.get("main_money", "N/A")
            if isinstance(main_money, (int, float)):
                if main_money > 0:
                    content_lines.append(f"- **主力资金**：净流入{main_money:.0f}万")
                else:
                    content_lines.append(f"- **主力资金**：净流出{abs(main_money):.0f}万")
            else:
                content_lines.append(f"- **主力资金**：{main_money}")
            
            hsgt_5d = stock.get("hsgt_5d", "N/A")
            if isinstance(hsgt_5d, (int, float)):
                if hsgt_5d > 0:
                    content_lines.append(f"- **北向资金**：近5日净流入{hsgt_5d:.0f}万")
                else:
                    content_lines.append(f"- **北向资金**：近5日净流出{abs(hsgt_5d):.0f}万")
            else:
                content_lines.append(f"- **北向资金**：{hsgt_5d}")
            
            holder_chg = stock.get("holder_chg", "N/A")
            if isinstance(holder_chg, (int, float)):
                if holder_chg < 0:
                    content_lines.append(f"- **股东户数**：较上期下降{abs(holder_chg):.1%}")
                else:
                    content_lines.append(f"- **股东户数**：较上期增长{holder_chg:.1%}")
            else:
                content_lines.append(f"- **股东户数**：{holder_chg}")
            
            block_premium = stock.get("block_premium", "无")
            content_lines.append(f"- **大宗交易**：近期{'有' if block_premium and '有' in block_premium else '无'}溢价成交")
            
            margin_ratio = stock.get("margin_ratio", "N/A")
            if isinstance(margin_ratio, (int, float)):
                content_lines.append(f"- **融资余额**：占流通市值{margin_ratio:.2%}")
            else:
                content_lines.append(f"- **融资余额**：{margin_ratio}")
            
            ai_conclusion = stock.get("ai_conclusion", "")
            if ai_conclusion:
                content_lines.append(f"- **AI核心结论**：{ai_conclusion}")
            
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
            
            content_lines.append("")
    
    content_lines.append("---")
    content_lines.append("⚠️ 量化系统生成，不构成投资建议")
    
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🔥 飞书推送更新 · 每日精选"},
            "subtitle": {"tag": "plain_text", "content": date_str},
            "template": "red" if selected_stocks else "blue"
        },
        "elements": [
            {"tag": "markdown", "content": "\n".join(content_lines)[:8000]},
            {"tag": "note", "elements": [{"tag": "plain_text",
                "content": f"StockAI Funnel · {datetime.now().strftime('%Y-%m-%d %H:%M')}"}]}
        ]
    }
    return _post({"msg_type": "interactive", "card": card})


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO,
                         format="%(asctime)s [%(levelname)s] %(message)s")
    
    print("[TEST 1] 发送市场全局汇总卡片...")
    ok1 = send_market_summary_card(
        trade_date="20260609",
        market_mode="防守",
        sh_index=3959.34,
        sh_pct=-0.85,
        up_count=1856,
        down_count=3348,
        total_stocks=5214,
        turnover=8560,
        limit_up_count=32,
        limit_down_count=8,
        ma5_ratio=0.42,
        main_sectors=["白酒", "电力", "金融"],
        backup_sectors=["光伏", "半导体"]
    )
    print(f"  市场汇总卡片: {'成功' if ok1 else '失败'}")
    
    print("[TEST 2] 发送板块汇总卡片...")
    ok2 = send_sector_summary_card(
        sectors=[
            {"name": "白酒", "limit_up_count": 5, "consecutive_count": 2, "signal_count": 3, "money_flow": 12.5},
            {"name": "电力", "limit_up_count": 8, "consecutive_count": 3, "signal_count": 5, "money_flow": 8.3},
            {"name": "金融", "limit_up_count": 3, "consecutive_count": 1, "signal_count": 2, "money_flow": -2.1},
        ],
        trade_date="20260609"
    )
    print(f"  板块汇总卡片: {'成功' if ok2 else '失败'}")
    
    print("[TEST 3] 发送精选金股卡片(v2)...")
    ok3 = send_stock_report_v2(
        ts_code="600519.SH",
        name="贵州茅台",
        total_score=88,
        python_score=62,
        ai_score=26,
        report_md="# 贵州茅台分析报告\n> **一句话决策**：建议逢低建仓",
        industry="白酒",
        close_price=1688.00,
        pct_chg=1.25,
        market_mode="防守",
        score_details={
            "主力资金": 15,
            "筹码结构": 15,
            "三日资金背离": 0,
            "振幅风险": -5
        },
        buy_range="¥1650 ~ ¥1700",
        stop_loss_main="¥1606",
        stop_loss_struct="¥1568",
        final_stop_loss="¥1568",
        position_defense="2%",
        position_attack="8%",
        logic_basis="资金面：主力资金当日逆势净流入，做多意愿较强；筹码面：股东户数连续下降，散户筹码向主力集中；行业面：所属行业无重大利空。",
        risks=[
            "短期风险：个股振幅过大，市场情绪波动易引发价格回撤",
            "潜在风险：存在限售股解禁压力，短期或出现筹码抛售"
        ],
        ai_analysis={
            "产业催化": {"score": 9, "comment": "行业无重大政策/题材催化"},
            "环境适配": {"score": 8, "comment": "个股走势适配当前市场环境"}
        }
    )
    print(f"  精选金股卡片(v2): {'成功' if ok3 else '失败'}")
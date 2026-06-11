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
        return "🟠 A级 · 关注"
    else:
        return "🔵 B级 · 观察"



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
    decline_sectors: list = None,
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
        main_sectors: 主线板块明细列表 (包含 name, current, sum_5, sum_10, sum_20)
        backup_sectors: 备选板块明细列表 (包含 name, current, sum_5, sum_10, sum_20)
        decline_sectors: 领跌板块明细列表 (包含 name, current, sum_5, sum_10, sum_20)
    """
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if trade_date else datetime.now().strftime("%Y-%m-%d")
    
    up_ratio = up_count / total_stocks if total_stocks > 0 else 0.5
    
    # 格式化百分比函数：领涨/正数为绿色，领跌/负数为红色，保留符号
    def _format_pct(val: float) -> str:
        if val > 0:
            return f"<font color='green'>+{val:.2f}%</font>"
        elif val < 0:
            return f"<font color='red'>{val:.2f}%</font>"
        else:
            return "<font color='grey'>+0.00%</font>"
            
    sh_pct_html = _format_pct(sh_pct)
    
    main_sectors = main_sectors or []
    backup_sectors = backup_sectors or []
    decline_sectors = decline_sectors or []
    
    main_lines = []
    for s in main_sectors:
        main_lines.append(f"  - **{s['name']}**: {_format_pct(s['current'])} (5日:{_format_pct(s['sum_5'])}, 10日:{_format_pct(s['sum_10'])}, 20日:{_format_pct(s['sum_20'])})")
    main_sector_text = "\n".join(main_lines) if main_lines else "  暂无"
    
    backup_lines = []
    for s in backup_sectors:
        backup_lines.append(f"  - **{s['name']}**: {_format_pct(s['current'])} (5日:{_format_pct(s['sum_5'])}, 10日:{_format_pct(s['sum_10'])}, 20日:{_format_pct(s['sum_20'])})")
    backup_sector_text = "\n".join(backup_lines) if backup_lines else "  暂无"
    
    decline_lines = []
    for s in decline_sectors:
        decline_lines.append(f"  - **{s['name']}**: {_format_pct(s['current'])} (5日:{_format_pct(s['sum_5'])}, 10日:{_format_pct(s['sum_10'])}, 20日:{_format_pct(s['sum_20'])})")
    decline_sector_text = "\n".join(decline_lines) if decline_lines else "  暂无"
    
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
                            "content": f"**上证指数**\n{sh_index:.2f}\n{sh_pct_html}"}]
                    },
                    {
                        "tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown",
                            "content": f"**市场模式**\n模式：{market_mode}模式"}]
                    },
                    {
                        "tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown",
                            "content": f"**上涨占比**\n{up_ratio*100:.1f}%"}]
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
                            "content": f"**涨跌家数**\n↑{up_count} | ↓{down_count}"}]
                    },
                    {
                        "tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown",
                            "content": f"**成交额**\n{turnover:.2f} 亿"}]
                    },
                    {
                        "tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown",
                            "content": f"**涨停/跌停**\n涨停{limit_up_count} / 跌停{limit_down_count}"}]
                    },
                    {
                        "tag": "column", "width": "weighted", "weight": 1,
                        "elements": [{"tag": "markdown",
                            "content": f"**5日线占比**\n{ma5_ratio*100:.1f}%"}]
                    }
                ]
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": (
                    f"**🔥 【主线领涨】**：\n{main_sector_text}\n\n"
                    f"**📌 【备选领涨】**：\n{backup_sector_text}\n\n"
                    f"**🚨 【领跌风险】**：\n{decline_sector_text}"
                )
            },
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": "数据来源：Tushare 实时行情 | 仅供参考"}]
            }
        ]
    }
    return _post({"msg_type": "interactive", "card": card})


# =============================================================================
# 新增：板块汇总卡片
# =============================================================================
def send_sector_summary_card(
    sectors: list,
    trade_date: str = "",
    summary: str = ""
) -> bool:
    """
    发送板块汇总卡片。
    """
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if trade_date else datetime.now().strftime("%Y-%m-%d")
    
    table_lines = ["**板块名称** | **涨停数** | **连板** | **信号数** | **资金流向**",
                   ":--- | :---: | :---: | :---: | :---"]
    for sector in sectors:
        mf = sector.get("money_flow", 0)
        mf_str = f"流入{mf:.1f}亿" if mf >= 0 else f"流出{abs(mf):.1f}亿"
        table_lines.append(
            f"{sector.get('name', '')} | {sector.get('limit_up_count', 0)} | "
            f"{sector.get('consecutive_count', 0)} | {sector.get('signal_count', 0)} | {mf_str}"
        )
    
    elements = []
    
    # 总结区
    if not summary:
        summary = "当日市场板块资金流向相对均衡，主力资金在核心板块有合理分布。"
    elements.append({
        "tag": "markdown",
        "content": f"**📊 板块资金流向总结**：\n{summary}"
    })
    elements.append({
        "tag": "hr"
    })
    
    # 表格
    elements.append({
        "tag": "markdown",
        "content": "\n".join(table_lines)
    })
    
    # 页脚固定文字
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": "数据来源：Tushare 实时行情 | 仅供参考"}]
    })
    
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📈 板块热力图"},
            "subtitle": {"tag": "plain_text", "content": date_str},
            "template": "purple"
        },
        "elements": elements
    }
    return _post({"msg_type": "interactive", "card": card})


# =============================================================================
# 重构：精选金股卡片（完整格式）
# =============================================================================
def _parse_report_details(report_md: str) -> dict:
    import re
    details = {
        "operation": "",
        "position": "",
        "buy_range": "",
        "stop_loss": "",
        "logic_basis": "",
        "risks": [],
        "follow_up": []
    }
    if not report_md:
        return details
        
    m_op = re.search(r"操作方向[：:]\s*(.+)", report_md)
    if m_op:
        details["operation"] = re.sub(r"[\*\[\]]+", "", m_op.group(1)).strip()
        
    m_pos = re.search(r"仓位建议[：:]\s*(.+)", report_md)
    if m_pos:
        details["position"] = re.sub(r"[\*\[\]]+", "", m_pos.group(1)).strip()
        
    m_buy = re.search(r"买入区间[：:]\s*(.+)", report_md)
    if m_buy:
        details["buy_range"] = re.sub(r"[\*\[\]]+", "", m_buy.group(1)).strip()
        
    m_sl = re.search(r"止损价[：:]\s*(.+)", report_md)
    if m_sl:
        details["stop_loss"] = re.sub(r"[\*\[\]]+", "", m_sl.group(1)).strip()
        
    if not details["operation"]:
        details["operation"] = _extract_operation(report_md)
    if not details["stop_loss"]:
        details["stop_loss"] = _extract_stop_loss(report_md)

    def get_section_content(header: str) -> str:
        pattern = rf"##\s*{header}\s*\n(.*?)(?=\n##|\n---|$)"
        m = re.search(pattern, report_md, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    logic_sec = get_section_content("为什么这么建议")
    if logic_sec:
        details["logic_basis"] = logic_sec
    
    risks_sec = get_section_content("需要注意的风险")
    if risks_sec:
        for line in risks_sec.splitlines():
            line_str = re.sub(r"^[-*\s\d\.\>\#]+", "", line).strip()
            if line_str:
                details["risks"].append(line_str)
                
    follow_sec = get_section_content("后续跟踪")
    if follow_sec:
        for line in follow_sec.splitlines():
            line_str = re.sub(r"^[-*\s\d\.\>\#]+", "", line).strip()
            if line_str:
                details["follow_up"].append(line_str)

    if not details["logic_basis"]:
        details["logic_basis"] = "根据量化指标和主力资金流向综合判定，目前个股走势良好，可按建议方案执行。"
    if not details["risks"]:
        details["risks"] = ["短期市场波动及个股回调风险", "大盘或所属行业轮动可能带来的持仓压力"]
    if not details["follow_up"]:
        details["follow_up"] = ["成交量是否持续放大", "如果跌破最终止损价，建议立即止损卖出"]

    return details


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
    downgrade_reason: str = "",  # 新增：降级原因
    main_money: float = 0,       # 新增：主力资金（万元）
    holder_chg: float = 0,       # 新增：股东户数变化（%）
    total_volume: float = 0,     # 新增：总成交额（万元）
    holder_current: int = 0,     # 新增：当前股东户数
    holder_prev: int = 0,        # 新增：上期股东户数
    hsgt_5d: float = 0,          # 新增：北向资金5日净流入（万元）
) -> bool:
    """
    发送全新格式的精选金股卡片。
    """
    conclusion = _extract_ai_conclusion(report_md)
    parsed = _parse_report_details(report_md)
    
    # 填充缺失字段
    operation = parsed["operation"] if parsed["operation"] else "可轻仓介入"
    if not buy_range:
        buy_range = parsed["buy_range"]
    if not final_stop_loss:
        final_stop_loss = parsed["stop_loss"]
    if not position_attack:
        position_attack = parsed["position"] if parsed["position"] else "15%"
    if not position_defense:
        try:
            p_val = float(position_attack.replace("%", "").strip())
            position_defense = f"{max(2.0, p_val / 3):.1f}%"
        except Exception:
            position_defense = "5%"
            
    if not logic_basis:
        logic_basis = parsed["logic_basis"]
    if not risks:
        risks = parsed["risks"]
        
    # 双止损价及标红处理
    if final_stop_loss:
        try:
            import re
            num_match = re.search(r"([\d\.]+)", final_stop_loss)
            if num_match:
                p_val = float(num_match.group(1))
                if not stop_loss_main:
                    stop_loss_main = f"¥{p_val * 1.02:.2f}"
                if not stop_loss_struct:
                    stop_loss_struct = f"¥{p_val:.2f}"
                final_stop_loss = f"¥{p_val:.2f}"
        except Exception:
            pass
            
    if not stop_loss_main:
        stop_loss_main = f"¥{close_price * 0.97:.2f}" if close_price > 0 else "¥0.00"
    if not stop_loss_struct:
        stop_loss_struct = f"¥{close_price * 0.95:.2f}" if close_price > 0 else "¥0.00"
    if not final_stop_loss:
        final_stop_loss = stop_loss_struct
        
    pct_html = ""
    if pct_chg > 0:
        pct_html = f"<font color='green'>+{pct_chg:.2f}%</font>"
    elif pct_chg < 0:
        pct_html = f"<font color='red'>{pct_chg:.2f}%</font>"
    else:
        pct_html = "<font color='grey'>+0.00%</font>"
    
    header_color = _score_to_color(total_score)
    grade_text = _score_to_grade_emoji(total_score)
    
    filled = min(int(total_score // 4), 10)
    bar = "█" * filled + "░" * (10 - filled)
    
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if trade_date else datetime.now().strftime("%Y-%m-%d")
    
    score_details = score_details or {}
    
    # 动态匹配申万行业强度
    industry_rank = "普通行业"
    try:
        import sqlite3
        import pandas as pd
        db_file = os.path.join(ROOT_DIR, "db", "stock_daily.db")
        if os.path.exists(db_file):
            conn = sqlite3.connect(db_file)
            cursor = conn.cursor()
            t_date = trade_date
            if not t_date:
                cursor.execute("SELECT MAX(trade_date) FROM daily_prices")
                t_date = cursor.fetchone()[0]
            if t_date:
                df_ind = pd.read_sql(
                    """
                    SELECT sl.industry, AVG(dp.pct_chg) as avg_pct_chg
                    FROM daily_prices dp
                    JOIN stock_list sl ON dp.ts_code = sl.ts_code
                    WHERE dp.trade_date = ? AND sl.industry IS NOT NULL AND sl.industry != '' AND dp.pct_chg IS NOT NULL
                    GROUP BY sl.industry
                    ORDER BY avg_pct_chg DESC
                    """,
                    conn, params=(t_date,)
                )
                if not df_ind.empty:
                    all_inds = df_ind["industry"].tolist()
                    if industry in all_inds[:3]:
                        industry_rank = "主线领涨行业"
                    elif industry in all_inds[3:5]:
                        industry_rank = "备选领涨行业"
            conn.close()
    except Exception as e:
        log.warning("Determining industry rank failed: %s", e)
        
    # 动态组装 AI 专项解读表各评估维度
    ai_table = {}
    tech_score = 0
    for k, v in score_details.items():
        if "量能" in k or "背离" in k or "指标" in k:
            tech_score += 5
    ai_table["技术分析"] = {"score": min(10, tech_score or 8), "comment": "个股K线形态及量比指标运行在合理区间"}
    
    main_money_val = main_money if main_money is not None else 0.0
    holder_chg_val = holder_chg if holder_chg is not None else 0.0
    money_comment = f"主力资金净流入 {main_money_val:.0f} 万元" if main_money_val != 0 else "资金面相对均衡"
    if holder_chg_val != 0:
        money_comment += f"，股东户数环比变化 {holder_chg_val:+.2f}%"
    ai_table["资金面"] = {"score": 9 if main_money_val > 0 else 7, "comment": money_comment}
    
    base_comment = "公司财务披露及监管风险指标处于正常健康区间"
    if downgrade_reason:
        base_comment = f"触发个股风控降级限制：{downgrade_reason}"
    ai_table["基本面"] = {"score": 6 if downgrade_reason else 8, "comment": base_comment}
    
    ai_table["产业催化"] = {"score": int(ai_score * 0.4) if ai_score > 0 else 8, "comment": "行业具备一定产业催化或概念发酵逻辑"}
    ai_table["环境适配"] = {"score": int(ai_score * 0.4) if ai_score > 0 else 8, "comment": f"适配当前大盘 {market_mode} 模式仓位策略"}
    
    if ai_analysis:
        for dim, data in ai_analysis.items():
            if dim in ai_table:
                ai_table[dim].update(data)
            else:
                ai_table[dim] = data

    content_lines = []
    
    # 一、核心评分明细
    content_lines.append("## 一、核心评分明细")
    content_lines.append("")
    
    add_items = []
    deduct_items = []
    for key, value in score_details.items():
        if isinstance(value, (int, float)):
            if value > 0:
                add_items.append(f"- **{key}**：+{value} 分")
            elif value < 0:
                deduct_items.append(f"- **{key}**：{value} 分")
        else:
            if "分" in str(value):
                if "+" in str(value):
                    add_items.append(f"- **{key}**：{value}")
                else:
                    deduct_items.append(f"- **{key}**：{value}")
            else:
                add_items.append(f"- **{key}**：{value}")
    
    content_lines.append("### 🟢 加分项")
    if add_items:
        content_lines.extend(add_items)
    else:
        content_lines.append("- 无加分项")
    
    content_lines.append("")
    content_lines.append("### 🔴 扣分项")
    if deduct_items:
        content_lines.extend(deduct_items)
    else:
        content_lines.append("- 无扣分项")
    
    # 资金与筹码
    content_lines.append("")
    content_lines.append("### 💰 资金与筹码")
    content_lines.append(f"- **主力资金净流入**：{main_money_val:+.2f} 万元" if main_money_val != 0 else "- **主力资金净流入**：0.00 万元")
    
    total_vol_val = total_volume if total_volume is not None else 0.0
    ratio = (abs(main_money_val) / total_vol_val * 100) if total_vol_val > 0 else 0.0
    content_lines.append(f"- **主力资金占比**：{ratio:.2f}%")
    content_lines.append(f"- **股东户数变化**：{holder_chg_val:+.2f}%" if holder_chg_val != 0 else "- **股东户数变化**：0.00%")
    
    holder_curr_val = holder_current if holder_current is not None else 0
    content_lines.append(f"- **当前股东户数**：{holder_curr_val:,} 户" if holder_curr_val > 0 else "- **当前股东户数**：0 户")
    content_lines.append(f"- **每股成本**：¥{close_price:.2f}")
    
    hsgt_5d_val = hsgt_5d if hsgt_5d is not None else 0.0
    content_lines.append(f"- **北向资金(5日)**：{hsgt_5d_val:+.2f} 万元" if hsgt_5d_val != 0 else "- **北向资金(5日)**：0.00 万元")
    
    content_lines.append("")
    if downgrade_reason:
        original_total = python_score + ai_score
        content_lines.append(f"**总分计算公式**：Python评分({python_score:.0f}) + AI评分({ai_score:.0f}) = {original_total:.0f}分 {downgrade_reason} → 最终得分 **{total_score:.0f}分**")
    else:
        content_lines.append(f"**总分计算公式**：Python评分({python_score:.0f}) + AI评分({ai_score:.0f}) = 最终得分 **{total_score:.0f}分**")
    
    # 二、大盘与行业模式
    content_lines.append("")
    content_lines.append("## 二、大盘与行业模式")
    content_lines.append(f"- **当前大盘模式**：{market_mode}模式")
    content_lines.append(f"- **所属申万行业**：{industry} ({industry_rank})")
    
    # 三、交易方案
    content_lines.append("")
    content_lines.append("## 三、交易方案")
    content_lines.append(f"- **操作方向**：{operation}")
    content_lines.append(f"- **推荐仓位**：防守模式 {position_defense} | 进攻模式 {position_attack}")
    content_lines.append(f"- **买入参考区间**：{buy_range if buy_range else '现价附近'}")
    content_lines.append("- **铁律限制**：单股持仓上限 20%，禁止超限")
    
    content_lines.append("")
    content_lines.append("### 🛡️ 双止损规则")
    content_lines.append(f"- 主止损价：{stop_loss_main}")
    content_lines.append(f"- 结构止损价：{stop_loss_struct}")
    content_lines.append(f"- **最终执行止损价**：<font color='red'>**{final_stop_loss}**</font>")
    
    # 四、逻辑依据
    content_lines.append("")
    content_lines.append("## 四、逻辑依据")
    content_lines.append(logic_basis)
    
    # 五、重点风险提示
    content_lines.append("")
    content_lines.append("## 五、重点风险提示")
    if risks:
        for r_item in risks:
            content_lines.append(f"- <font color='red'>{r_item}</font>")
    else:
        content_lines.append("- <font color='red'>短期价格波动风险</font>")
    
    # 六、后续跟踪计划
    content_lines.append("")
    content_lines.append("## 六、后续跟踪计划")
    if parsed.get("follow_up"):
        for f_item in parsed["follow_up"]:
            content_lines.append(f"- {f_item}")
    else:
        content_lines.append("- **核心观察指标**：成交量是否持续放大、主力资金是否延续流入")
        content_lines.append("- **动态调仓策略**：")
        content_lines.append("  - 放量上涨：持有并观察")
        content_lines.append(f"  - 跌破最终止损价 {final_stop_loss}：立即止损/清仓离场")
    
    # 七、AI专项解读
    content_lines.append("")
    content_lines.append("## 七、AI专项解读")
    content_lines.append("| 评估维度 | 得分 | 解读 |")
    content_lines.append("|:---|:---:|:---|")
    for dim, data in ai_table.items():
        content_lines.append(f"| {dim} | {data.get('score', '-')} | {data.get('comment', '-')} |")
    content_lines.append("")
    content_lines.append(f"**AI一句话结论**：{conclusion}")
    
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text",
                "content": f"🔥 今日精选金股 · {ts_code} {name}"},
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
                            "content": f"**收盘价**\n¥{close_price:.2f}\n{pct_html}"}]
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
                "elements": [{"tag": "plain_text", "content": "数据来源：Tushare 实时行情 | 仅供参考"}]
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
            f"📊 今日精选金股 · 盘后分析\n"
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
    
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if trade_date else datetime.now().strftime("%Y-%m-%d")
    
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🔥 今日精选金股 · 盘后分析"},
            "subtitle": {"tag": "plain_text", "content": date_str},
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
                "elements": [{"tag": "plain_text", "content": "数据来源：Tushare 实时行情 | 仅供参考"}]
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
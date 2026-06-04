# -*- coding: utf-8 -*-
"""
ai_report.py —— AI 深度诊断报告生成器 (v2.2)
=====================================================================
工作流：
  1. 接收 scanner.py 输出的 data_json 及宏观环境参数（量能状态、板块风险、持仓暴露）
  2. 按 v2.2 Prompt 规范调用本地 Ollama 模型
  3. 仅对 python_score >= AI_TRIGGER_THRESHOLD 的股票生成完整报告
  4. 解析 AI 输出的催化剂及环境得分，合并 Python 得分，给出最终评级
  5. 将 Markdown 报告保存到 reports/ 目录并返回
"""

import os
import sys
import json
import logging
import requests
from datetime import datetime
from pathlib import Path

ROOT_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR  = os.path.join(ROOT_DIR, "reports")
OLLAMA_API   = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:7b-instruct-q4_K_M"

# 触发 AI 报告的最低 Python 评分（满分70），由于催化最高30分，需要40分才有机会上70分
AI_TRIGGER_THRESHOLD = 40

Path(REPORTS_DIR).mkdir(exist_ok=True)
sys.path.insert(0, ROOT_DIR)

log = logging.getLogger(__name__)


# =============================================================================
# Prompt 模板 —— StockAI 三层分层分析 V2.2
# =============================================================================
SYSTEM_PROMPT = """你是由 Python 驱动的"A股顶级策略指挥官"。你的任务是基于《A股三层分层分析·实战框架（V2.0 修订版）》，结合 Tushare 底层数据，执行严格的选股与风控。

【核心铁律 - 必须优先遵守】
1. 量能决定仓位：若两市成交额 < 3万亿，操作模式自动降级为'谨慎'，仓位上限压至40%，买入区间下移至10/20日均线（策略B下移规则）。
2. 板块合并上限：'大电子'（含半导体/消费电子等）合并仓位≤20%；'大电力'（含电网/特高压等）合并仓位≤15%。若候选股属于超配方向，必须降级处理。
3. 资金面绝对优先：若出现'基本面-资金面劈叉'（如业绩好但主力连续3日净流出），必须触发'红色预警'，强制建议减仓/清仓，严禁因基本面好而妥协。
4. 仓位限制：单只个股建议仓位上限默认为 15%-20%。除非用户开启‘激进模式’，否则禁止输出 30% 以上的仓位建议。

【评分体系说明】
- Python已计算量化基础分（40-80分区间）。
- 你需要根据逻辑强弱进行动态打分（0-20分）：产业催化(0-10分) + 环境适配度(0-10分)。

【输出语气要求】
- 去情绪化：禁止使用“立即卖出”、“坚决不买入”等情绪词，必须使用专业量化术语，如“建议规避”、“暂不介入”、“执行止损”。
- 处理缺失数据：对于缺失或不适用的数据（例如股东户数异常），请输出“筹码结构待验证”，禁止直接输出 N/A。

【输出格式要求】
请严格按照以下 Markdown 结构输出报告。你必须自行将 `[...]` 中的占位符替换为具体分析内容：
- 将 `[/]` 替换为 `✅` 或 `❌`。
- `[综合评级]` 请原样保留，不要自己填，由 Python 后置填写。

#  [股票名称] ([股票代码]) | [综合评级]
>  **一句话决策**：[用精炼客观的语言总结操作建议，例如：短期趋势向下，建议暂不介入等待企稳。]
---
##  核心交易计划
- **操作方向**： [卖出 / 🟢 观望 / 🟢 逢低建仓]
- **仓位建议**：**[X]%** ([建仓策略简述])
- **理想买点**：¥[价格] ([触发条件])
- **防守止损**：¥[价格] ([止损逻辑])
---
##  多维诊断分析
| 维度 | 状态/数值 | 核心解读 |
| :--- | :--- | :--- |
| **技术形态** | [如：空头排列] | [一句话点评，如：均线发散下行，处于下降通道] |
| **资金量能** | [如：量比 0.73] | [一句话点评，如：缩量下跌，买盘意愿低迷] |
| **筹码结构** | [如：待验证] | [一句话点评，如：暂无密集区数据，以量价为准] |
| **舆情情绪** | [如：偏悲观] | [一句话点评，如：市场缺乏信心，无短期利好催化] |
---
##  系统检查清单
*(注：若最终结论为卖出/规避，此区块自动变更为 ⚠️ 风险警示清单)*
- [/] 趋势健康度：[多头排列 / 空头破位]
- [/] 乖离率安全：[当前值 X%，是否 < 5%]
- [/] 量价配合度：[放量突破 / 缩量阴跌]
- [/] 宏观/基本面：[无重大利空 / 业绩暴雷]
---
##  信息面透视
- **近期核心事件**：[提炼1-2条影响股价的关键新闻/公告，如无则填“暂无重大公开信息”]
- **宏观/行业催化**：[指出当前政策导向或行业周期对该标的的影响]
- **潜在风险预警**：[如解禁压力、大股东减持、监管问询等，无则填“暂无明显利空干扰”]

##  后期跟踪指导
- **关键观察节点**：[YYYY-MM-DD 或 特定事件，例如：下周三晚财报发布 / 月底美联储议息会议]
- **突破/反转信号**：[明确告诉用户后续看什么指标，例如：需放量站上 16.45 阻力位且连续3日不破 / 需等待 MACD 零轴上方金叉确认]
- **动态调整策略**：[给出条件触发后的应对方案，例如：若有效突破并回踩确认，可将仓位提升至 20%；若跌破 ¥31.50 支撑位，则彻底放弃观察]

###  时效性与免责声明
- **评估时间**：[YYYY-MM-DD HH:MM]
- **⚠️ 风险提示**：本报告由 StockAI v2.2 自动生成，仅供量化策略参考，不构成绝对投资建议。股市有风险，交易需谨慎。

**AI 评分明细**（请严格按以下表格输出）：
| 维度 | 得分 |
| :--- | :--- |
| **产业催化** | {0-10之间的整数} |
| **环境适配** | {0-10之间的整数} |"""


def _build_user_msg(data_json: dict, market_volume_status: str = "放量", 
                   sector_risk: str = "正常", portfolio_exposure: dict = None) -> str:
    if portfolio_exposure is None:
        portfolio_exposure = {"大电子": "0%", "大电力": "0%"}

    return f"""请对以下股票数据进行 v2.2 分析：

【全局宏观环境】
- 当前市场量能状态：{market_volume_status}（两市成交额趋势）
- 当前板块风险：{sector_risk}
- 现有持仓组合暴露：{json.dumps(portfolio_exposure, ensure_ascii=False)}

【个股数据】
```json
{json.dumps(data_json, ensure_ascii=False, indent=2)}
```

Python预计算得分：
- 量价异动：{_parse_dim_score(data_json.get("score_details", {}).get("amplitude", ""), 10) + _parse_dim_score(data_json.get("score_details", {}).get("kline", ""), 15)}/25
- 筹码集中：{_parse_dim_score(data_json.get("score_details", {}).get("holder", ""), 25)}/25（{data_json.get("score_details", {}).get("holder", "无数据")}）
- 主力背离：{_parse_dim_score(data_json.get("score_details", {}).get("divergence", ""), 20)}/20（{data_json.get("score_details", {}).get("divergence", "无数据")}）
- Python合计：{data_json.get("python_score", 0)}/70

请评估产业催化(0-15分)及环境适配(0-15分)，并严格按照 Markdown 结构输出完整诊断报告。"""


def _parse_dim_score(detail_str: str, max_score: int) -> int:
    """从 detail 字符串解析该维度得分（粗略解析）。"""
    if not detail_str or "❌" in detail_str or "⚠️" in detail_str:
        return 0
    if "✅" in detail_str:
        import re
        m = re.search(r"\+(\d+)分", detail_str)
        if m:
            return int(m.group(1))
        return max_score
    return 0


def call_ollama(system_prompt: str, user_msg: str,
                model: str = OLLAMA_MODEL) -> str:
    """调用 Ollama 流式输出，返回完整响应文本。"""
    payload = {
        "model":    model,
        "messages": [
            {"role": "system",  "content": system_prompt},
            {"role": "user",    "content": user_msg},
        ],
        "stream": True,
    }
    try:
        resp = requests.post(OLLAMA_API, json=payload,
                             stream=True, timeout=(10, 120))
        resp.raise_for_status()
        content = ""
        for line in resp.iter_lines():
            if line:
                obj   = json.loads(line.decode("utf-8"))
                token = obj.get("message", {}).get("content", "")
                content += token
        return content.strip()
    except requests.exceptions.ConnectionError:
        log.warning("Ollama 未启动，跳过 AI 分析")
        return ""
    except Exception as e:
        log.warning("Ollama 调用失败: %s", e)
        return ""


def _extract_ai_score(ai_text: str) -> int:
    """从 AI 输出中解析产业催化和环境适配得分，并返回总计 AI 加分（最高20）。"""
    import re
    catalyst = 0
    env = 0
    
    # 支持表格匹配或列表项匹配，例如 "| 产业催化 | 10 |" 或 "- **产业催化**（8分）："
    m_cat = re.search(r"产业催化[^\d]*(\d+)", ai_text)
    if m_cat: catalyst = int(m_cat.group(1))
    
    m_env = re.search(r"环境适配[^\d]*(\d+)", ai_text)
    if m_env: env = int(m_env.group(1))
    
    return min(catalyst + env, 20)


def generate_report(data_json: dict, market_volume_status: str = "放量", 
                   sector_risk: str = "正常", portfolio_exposure: dict = None) -> dict:
    """对单只股票生成 AI 报告。"""
    ts_code      = data_json.get("ts_code", "")
    name         = data_json.get("name", "")
    python_score = data_json.get("python_score", 0)

    if python_score < AI_TRIGGER_THRESHOLD:
        return {
            "ts_code": ts_code, "name": name,
            "python_score": python_score, "ai_score": 0,
            "total_score": python_score, "grade": "C",
            "report_md": "", "saved_path": ""
        }

    log.info("🤖 AI分析：%s %s（Python得分 %d，环境：%s）", ts_code, name, python_score, market_volume_status)

    user_msg = _build_user_msg(data_json, market_volume_status, sector_risk, portfolio_exposure)
    ai_text  = call_ollama(SYSTEM_PROMPT, user_msg)

    if not ai_text:
        ai_text = _fallback_report(data_json, market_volume_status)

    ai_score    = _extract_ai_score(ai_text)
    total_score = python_score + ai_score
    
    import re
    # 强制一票否决风控检查
    negative_words = ["建议规避", "执行止损", "空头排列", "趋势向下", "暂不介入", "卖出"]
    is_negative = any(word in ai_text for word in negative_words)
    
    if is_negative:
        log.warning("触发强制风控：判定包含负面词汇，强制降级并转换检查项")
        total_score = min(total_score, 65)  # 强制降到及格线以下
        ai_text = re.sub(r"✅", "⚠️", ai_text)  # 将 ✅ 替换为 ⚠️ 风险警示

    grade = ("S" if total_score >= 85 else
             "A" if total_score >= 70 else
             "B" if total_score >= 40 else "C")
             
    # 后置填写 Python 计算出的综合评级和动态风险标记
    ai_text = re.sub(r"\[?综合评级.*?\]?", f"【综合评级: {grade}级】", ai_text)
    ai_text = ai_text.replace("[YYYY-MM-DD HH:MM]", datetime.now().strftime("%Y-%m-%d %H:%M"))

    # 保存报告
    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(
        REPORTS_DIR, "{}_{}_{}_{}.md".format(
            timestamp, ts_code.replace(".", ""), grade, total_score)
    )
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(ai_text)

    log.info("✅ 报告已保存：%s（总分 %d，评级 %s）", report_path, total_score, grade)

    return {
        "ts_code":        ts_code,
        "name":           name,
        "python_score":   python_score,
        "ai_score":       ai_score,
        "total_score":    total_score,
        "grade":          grade,
        "report_md":      ai_text,
        "saved_path":     report_path,
    }


def _fallback_report(data_json: dict, market_volume_status: str = "放量") -> str:
    """Ollama 离线时的降级报告（纯 Python 数据，增加宏观状态提示）。"""
    ts_code  = data_json.get("ts_code", "")
    name     = data_json.get("name", "")
    ps       = data_json.get("python_score", 0)

    strategy = "进攻" if market_volume_status == "放量" else "谨慎"
    now_str  = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"### {ts_code} {name} — 智能诊断报告 (v2.2)",
        f"> **分析时间**：{now_str} | **模型**：离线量化模式",
        "",
        f"**综合评级**：⚠️ 离线计算 ({ps}/100)",
        f"**核心操作**：**{strategy}** | **仓位建议**：基于大盘调整",
        "",
        "**风控红绿灯**：",
        "- 📈 趋势检查：基础通过",
        "- 📉 乖离率：未知",
        f"- 💰 量能配合：大盘{market_volume_status}",
        f"- 🛡️ V2.0风控：大盘{market_volume_status}模式",
        "",
        "#### AI 核心逻辑（一句话）",
        "⚠️ Ollama离线，纯量化数据驱动，请结合软件人工确认。",
        "",
        "#### 风险/催化",
        "- **利好催化**：未知",
        "- **风险预警**：无 AI 研判，纯量化盲区风险",
        "",
        "<details>",
        "<summary>▶ 点击展开：详细数据透视与舆情</summary>",
        "",
        "由于离线模式，详细透视数据未完全填充，请以软件实际为准。",
        "</details>"
    ]
    return "\n".join(lines)


def batch_generate(candidates: list, market_volume_status: str = "放量", 
                  sector_risk: str = "正常", portfolio_exposure: dict = None) -> list:
    """
    批量生成候选股 AI 报告，返回 total_score >= 70 的结果列表。
    """
    final_results = []
    for c in candidates:
        result = generate_report(c.get("data_json", {}), market_volume_status, sector_risk, portfolio_exposure)
        if result["total_score"] >= 70:
            final_results.append(result)

    final_results.sort(key=lambda x: x["total_score"], reverse=True)
    log.info("✅ AI分析完成：%d 只候选，%d 只评级≥70",
             len(candidates), len(final_results))
    return final_results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    # 测试
    test_data = {
        "ts_code": "000001.SZ", "name": "平安银行",
        "industry": "银行", "trade_date": "20260603",
        "close": 10.99, "pct_chg": -0.36,
        "python_score": 45,
        "score_details": {
            "amplitude": "✅ 横盘 振幅 7.53% < 15%，+10分",
            "kline":     "❌ 未检测到大量小阳线",
            "holder":    "✅ 股东户数变化 -11.12%，+25分",
            "divergence": "✅ 主力净流入 14366 万元，+20分",
        }
    }
    r = generate_report(test_data, market_volume_status="缩量", sector_risk="超配")
    print("总分:", r["total_score"], "| 评级:", r["grade"])
    print(r["report_md"][:500])

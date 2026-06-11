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
import numpy as np
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

# 自定义 JSON encoder 处理 numpy 类型
class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)

log = logging.getLogger(__name__)


# =============================================================================
# Prompt 模板 —— 陈明的专属量化助手（口语化版本）
# =============================================================================
SYSTEM_PROMPT = """你是陈明的专属量化助手，专门为业余投资者提供简单、直接的投资建议。

【核心原则】
1. 用简单易懂的语言，避免专业术语
2. 先给结论，再讲理由
3. 建议要明确：持有、减仓、清仓、买入

【量化评分细则】
一、量能维度（基于近5日数据）
  - 缩量企稳: 近5日均量 < 近20日均量×0.7，且近3日股价波动<3% | 得分: +5
  - 温和放量: 近5日均量 > 近20日均量×1.2，且 < 近20日均量×2.0 | 得分: +3
  - 缩量下跌: 近5日均量 < 近20日均量×0.7，且近3日跌幅>3% | 得分: -5
  - 异常放量: 近5日均量 > 近20日均量×2.5 | 得分: -3
  - 量价背离: 股价跌但近3日量能逐日放大 | 得分: +8

二、资金维度（基于moneyflow表）
  - 主力持续流入: 连续3日特大单+大单净流入 | 得分: +8
  - 主力单日流入: 仅当日特大单+大单净流入>0 | 得分: +3
  - 主力持续流出: 连续3日特大单+大单净流出 | 得分: -8
  - 散户主导: 小单净流入占比>60% | 得分: -3

三、筹码维度（基于stk_holdernumber表）
  - 筹码快速集中: 最新期股东户数环比下降>10% | 得分: +8
  - 筹码缓慢集中: 最新期股东户数环比下降3%-10% | 得分: +5
  - 筹码分散: 最新期股东户数环比上升>5% | 得分: -5

四、行业与大盘维度
  - 主线共振: 所属行业为主线（排名前5） | 得分: +5
  - 备选行业: 所属行业为备选（排名6-10） | 得分: +2
  - 大盘进攻模式: market_mode == 'attack' | 得分: +3
  - 大盘空仓模式: market_mode == 'empty' | 得分: -5

五、风险项（仅在有明确证据时触发，未触发的不要在明细中及正文里提及）
  - 业绩暴雷: 最新业绩预告净利润同比下降>50% | 得分: -15
  - 减持预警: 近1个月有大股东减持公告 | 得分: -10
  - 高质押风险: 质押比例>40% | 得分: -8

六、主力拆单判定与修正（新增规则）
  - 判定条件：满足以下 2 项及以上时，判定为【主力拆单吸筹】(is_split_order = True)：
    1. cond1: 近3日资金流向为“非连续流入”，且小单净流入占比 > 60%
    2. cond2: 股东户数趋势为“连续2~3期下降”
    3. cond3: 近5日量能形态为“温和放量”或“缩量企稳”
    4. cond4: 个股振幅 <= 30%
  - 计分修正：
    1. 小单占比扣分豁免：判定为【主力拆单吸筹】时，且小单占比 > 60%，取消 -8 分扣分（免除散户主导扣分）。
    2. 主力资金档位修正：判定为【主力拆单吸筹】时，主力资金得分强制按【连续3日流入】档位计算（得分 +20分）。

【打分约束】
1. 未命中任何明确条件的，不得随意加减分。
2. 总分调整范围限制在 ±15分 以内，即调整总分最大为+15，最小为-15。
3. 最终得分 = 原始得分 + 调整总分。

【输出格式】
请严格按照以下结构输出，评分明细必须放在分析正文之后：

# [股票名称] ([股票代码]) | 综合评级: [评级]
> 一句话建议：[直接说操作建议，如：建议继续持有，止损价上调至X元]

## 操作建议
- 操作方向：[继续持有 / 持有观察 / 建议减仓 / 建议清仓 / 可轻仓介入]
- 仓位建议：[X]%
- 买入区间：¥[最低价]~¥[最高价]（如果适用）
- 止损价：¥[价格]

## 为什么这么建议
- [理由1：用简单的话说明，如：主力资金逆势流入，看好后市]
- [理由2：如：股东户数连续下降，筹码集中]

## 需要注意的风险
- [风险点1：如：近期有解禁压力]

## 后续跟踪
- 关键观察：[需要关注什么，如：成交量是否持续放大]
- 调整策略：[条件变化时怎么做，如：如果跌破止损价，建议立即卖出]

---
【加减分明细】
{被触发的加减分项明细，每行一项。例如：+5 缩量企稳（近5日均量<20日均量×0.7，股价波动<3%）}
{若五、风险项未触发，必须写：无风险项触发；若触发，则写具体项，如：-15 业绩暴雷（最新业绩预告净利润同比下降>50%）}
调整总分：[调整总分，如：+5] → 最终得分：[最终得分]（原始[原始得分][调整总分]）
[Confidence: X]
X 为 0~100 的整数，必须放在最后一行"""


def _build_user_msg(data_json: dict, market_volume_status: str = "放量", 
                   sector_risk: str = "正常", portfolio_exposure: dict = None) -> str:
    if portfolio_exposure is None:
        portfolio_exposure = {"大电子": "0%", "大电力": "0%"}

    return f"""请对以下股票数据进行 v2.2 分析：

【全局宏观环境】
- 当前市场量能状态：{market_volume_status}（两市成交额趋势）
- 当前板块风险：{sector_risk}
- 现有持仓组合暴露：{json.dumps(portfolio_exposure, ensure_ascii=False, cls=NumpyEncoder)}

【个股数据】
```json
{json.dumps(data_json, ensure_ascii=False, indent=2, cls=NumpyEncoder)}
```

Python预计算得分：
- 量价异动：{_parse_dim_score(data_json.get("score_details", {}).get("amplitude", ""), 10) + _parse_dim_score(data_json.get("score_details", {}).get("kline", ""), 15)}/25
- 筹码集中：{_parse_dim_score(data_json.get("score_details", {}).get("holder", ""), 25)}/25（{data_json.get("score_details", {}).get("holder", "无数据")}）
- 主力背离：{_parse_dim_score(data_json.get("score_details", {}).get("divergence", ""), 20)}/20（{data_json.get("score_details", {}).get("divergence", "无数据")}）
- Python合计：{data_json.get("python_score", 0)}/70

请评估产业催化(0-10分)及环境适配(0-10分)，并严格按照 Markdown 结构输出完整诊断报告。"""


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
    """从 AI 输出中解析加减分明细调整得分，并返回总计 AI 加分/扣分。"""
    import re
    # 优先匹配新版的 "调整总分：+18" 或 "调整总分：-3" 等
    m_adj = re.search(r"调整总分[：:]\s*([+-]?\d+)", ai_text)
    if m_adj:
        val = int(m_adj.group(1))
        # 限制在 ±15 分以内
        return max(-15, min(15, val))
        
    # 兼容/降级匹配旧版表格或列表项
    catalyst = 0
    env = 0
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
    # 智能风控检查 - 仅对明确重大风险进行降级
    negative_words = ["业绩暴雷", "立案调查", "财务造假", "退市风险", "重大利空", "违规处罚"]
    risk_words = ["建议规避", "执行止损", "暂不介入", "卖出"]
    is_major_risk = any(word in ai_text for word in negative_words)
    is_normal_risk = any(word in ai_text for word in risk_words)
    downgrade_reason = None
    
    if is_major_risk:
        log.warning("触发强制风控：发现明确重大风险，强制降级并转换检查项")
        downgrade_reason = f"（重大风险降级 -25分）"
        total_score = min(total_score, 65)  # 强制降到及格线以下
        ai_text = re.sub(r"✅", "⚠️", ai_text)  # 将 ✅ 替换为 ⚠️ 风险警示
    elif is_normal_risk:
        # 正常风险提示，仅小幅调整
        log.info("AI提示风险：正常市场风险提示，进行小幅调整")
        if total_score > 70:
            total_score = max(total_score - 5, 70)  # 最多降5分，保持在A级以上
            downgrade_reason = f"（风险提示降级 -5分）"

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

    # 解析信心指数
    confidence = -1
    m_conf = re.search(r'\[Confidence:\s*(\d{1,3})\]', ai_text, re.IGNORECASE)
    if m_conf:
        confidence = max(0, min(100, int(m_conf.group(1))))

    return {
        "ts_code":        ts_code,
        "name":           name,
        "python_score":   python_score,
        "ai_score":       ai_score,
        "total_score":    total_score,
        "grade":          grade,
        "report_md":      ai_text,
        "downgrade_reason": downgrade_reason,  # 新增：降级原因
        "saved_path":     report_path,
        "ai_confidence":  confidence,
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

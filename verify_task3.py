import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 验证 parse_confidence 函数
from analyze_stock import parse_confidence, build_ollama_prompt

# 模拟 AI 输出，含正确格式标签
test_outputs = [
    "...分析文本...\n[Confidence: 82]",
    "...分析文本...\n[confidence: 45]",
    "...没有标签的输出...",
    "[Confidence: 150]",    # 超出范围
    "[Confidence: -5]",     # 负数
]

print("=== parse_confidence 验证 ===")
for txt in test_outputs:
    c = parse_confidence(txt)
    print(f"  输入: {repr(txt[-40:])} -> 信心指数: {c}")

# 验证 prompt 包含第11条
sc = {"volume_price": 15, "chip_structure": 15, "market_behavior": 0,
      "catalyst": 0, "risk_flag": False, "total_score": 30}
prompt = build_ollama_prompt("000001.SZ", sc, ["主力净流入 +15", "筹码集中 +15"])
has_conf_tag = "[Confidence:" in prompt
has_rule_90  = "90~100" in prompt
has_v33      = "v3.3" in prompt

print()
print("=== build_ollama_prompt 验证 ===")
print(f"  包含 v3.3 基线描述: {has_v33}")
print(f"  包含 [Confidence: X] 要求: {has_conf_tag}")
print(f"  包含评分规则 90~100: {has_rule_90}")

if has_conf_tag and has_v33 and has_rule_90:
    print("\n✅ 任务3 验证通过！AI信心指数机制已正确植入")
else:
    print("\n❌ 任务3 验证失败，请检查")

# 打印 prompt 最后10行
print("\n--- Prompt 结尾（最后12行）---")
for line in prompt.split("\n")[-12:]:
    print(f"  {line}")

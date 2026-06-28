#!/usr/bin/env bash
# 参数扫描脚本：top_pct × hold_days 网格搜索 + 2026样本外验证
# 结果写入 reports/param_scan_results.tsv

set -euo pipefail
cd /Users/lyu/Three-Layer-Decision-Framework

RESULT_FILE="reports/param_scan_results.tsv"
mkdir -p reports

# 写表头
echo -e "type\ttop_pct\thold_days\ttrades\twin_rate\tavg_ret\ttotal_ret\tannual_ret\tmax_dd\tsharpe" > "$RESULT_FILE"

# 解析单次回测输出，提取关键指标
extract_metrics() {
    local output="$1"
    local trades win_rate avg_ret total_ret annual_ret max_dd sharpe
    trades=$(echo "$output"    | grep "交易总笔数"   | grep -oE '[0-9]+' | head -1)
    win_rate=$(echo "$output"  | grep "信号平均胜率" | grep -oE '[0-9]+\.[0-9]+' | head -1)
    avg_ret=$(echo "$output"   | grep "单笔均收益"   | grep -oE '[-+]?[0-9]+\.[0-9]+' | head -1)
    total_ret=$(echo "$output" | grep "组合总收益率" | grep -oE '[-+]?[0-9]+\.[0-9]+' | head -1)
    annual_ret=$(echo "$output"| grep "组合年化收益" | grep -oE '[-+]?[0-9]+\.[0-9]+' | head -1)
    max_dd=$(echo "$output"    | grep "组合最大回撤" | grep -oE '[-+]?[0-9]+\.[0-9]+' | head -1)
    sharpe=$(echo "$output"    | grep "组合夏普比率" | grep -oE '[-+]?[0-9]+\.[0-9]+' | head -1)
    echo -e "${trades:-?}\t${win_rate:-?}\t${avg_ret:-?}\t${total_ret:-?}\t${annual_ret:-?}\t${max_dd:-?}\t${sharpe:-?}"
}

echo "================================================================"
echo " 第一步：top_pct 扫描（hold_days=10，2025全年）"
echo "================================================================"
for pct in 10 15 20 25; do
    echo -n "→ top_pct=${pct}% ... "
    OUT=$(python backtest_ml_strategy.py \
        --start 20250101 --end 20251231 \
        --top_pct ${pct} --hold_days 10 \
        --output /dev/null 2>&1)
    METRICS=$(extract_metrics "$OUT")
    echo -e "in_sample\t${pct}\t10\t${METRICS}" >> "$RESULT_FILE"
    SHARPE=$(echo "$METRICS" | cut -f7)
    ANNUAL=$(echo "$METRICS" | cut -f5)
    echo "年化=${ANNUAL}% 夏普=${SHARPE}"
done

echo ""
echo "================================================================"
echo " 第二步：hold_days 扫描（top_pct=15，2025全年）"
echo "================================================================"
for days in 7 12 15; do   # 10天已在上方跑过
    echo -n "→ hold_days=${days}天 ... "
    OUT=$(python backtest_ml_strategy.py \
        --start 20250101 --end 20251231 \
        --top_pct 15 --hold_days ${days} \
        --output /dev/null 2>&1)
    METRICS=$(extract_metrics "$OUT")
    echo -e "in_sample\t15\t${days}\t${METRICS}" >> "$RESULT_FILE"
    SHARPE=$(echo "$METRICS" | cut -f7)
    ANNUAL=$(echo "$METRICS" | cut -f5)
    echo "年化=${ANNUAL}% 夏普=${SHARPE}"
done

echo ""
echo "================================================================"
echo " 第三步：2026 样本外验证（当前最优参数 top15% + 10天）"
echo "================================================================"
echo -n "→ 2026年至今 ... "
OUT_2026=$(python backtest_ml_strategy.py \
    --start 20260101 --end 20260625 \
    --top_pct 15 --hold_days 10 \
    --output /dev/null 2>&1)
METRICS_2026=$(extract_metrics "$OUT_2026")
echo -e "out_of_sample\t15\t10\t${METRICS_2026}" >> "$RESULT_FILE"
SHARPE_26=$(echo "$METRICS_2026" | cut -f7)
ANNUAL_26=$(echo "$METRICS_2026" | cut -f5)
echo "年化=${ANNUAL_26}% 夏普=${SHARPE_26}"

echo ""
echo "================================================================"
echo " 扫描完成！结果已写入 $RESULT_FILE"
echo "================================================================"
cat "$RESULT_FILE"

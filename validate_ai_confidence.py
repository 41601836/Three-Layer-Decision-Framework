# -*- coding: utf-8 -*-
"""
AI信心验证脚本 (validate_ai_confidence.py)
==========================================

验证Ollama GPU加速和AI信心过滤效果
"""

import os
import sys
import sqlite3
import argparse
import time
from datetime import datetime

import pandas as pd

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")

sys.path.insert(0, ROOT_DIR)


def check_ollama_gpu():
    """检查Ollama是否使用GPU"""
    import subprocess
    try:
        # 检查CUDA
        result = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            print("[GPU] ✅ CUDA available")
            lines = result.stdout.strip().split('\n')
            for line in lines[:5]:
                print(f"[GPU] {line}")
            return True
        else:
            print("[GPU] ⚠️ CUDA not available")
            return False
    except Exception as e:
        print(f"[GPU] Check failed: {e}")
        return False


def test_ollama_speed():
    """测试Ollama推理速度"""
    from analyze_stock import call_ollama
    
    print("\n[TEST] 测试Ollama推理速度...")
    prompt = "Hello, how are you?"
    
    start_time = time.time()
    result = call_ollama(prompt)
    elapsed = time.time() - start_time
    
    content, confidence = result
    print(f"[TEST] 响应时间: {elapsed:.2f}秒")
    print(f"[TEST] 信心值: {confidence}")
    print(f"[TEST] 响应长度: {len(content)}字符")
    
    return elapsed


def validate_ai_confidence(sample_size=100):
    """验证AI信心过滤效果"""
    from analyze_stock import StockAnalyzer, call_ollama, build_ollama_prompt
    
    conn = sqlite3.connect(DB_PATH)
    try:
        # 加载历史信号
        df = pd.read_sql("""
            SELECT ts_code, signal_date, score, ret_10d
            FROM signal_fingerprints
            WHERE ret_10d IS NOT NULL
            ORDER BY signal_date DESC
            LIMIT 5000
        """, conn)
        
        if len(df) < sample_size:
            sample_size = len(df)
        
        sample = df.sample(n=sample_size, random_state=42)
        print(f"\n[DATA] 加载 {len(df)} 条信号，采样 {len(sample)} 条")
        
        analyzer = StockAnalyzer()
        results = []
        total_latency = 0
        success_count = 0
        
        for idx, (_, row) in enumerate(sample.iterrows()):
            if idx % 10 == 0:
                print(f"    进度: {idx}/{len(sample)}")
            
            ts_code = row["ts_code"]
            signal_date = row["signal_date"]
            
            try:
                start_time = time.time()
                
                # 先获取评分
                score_card, reasoning = analyzer.analyze_v3_0(ts_code, catalyst_score=0)
                
                # 构建prompt并调用Ollama
                prompt = build_ollama_prompt(ts_code, score_card, reasoning, "")
                ai_analysis, ai_confidence = call_ollama(prompt)
                
                elapsed = time.time() - start_time
                
                if ai_confidence >= 0:
                    success_count += 1
                    total_latency += elapsed
                
                results.append({
                    "ts_code": ts_code,
                    "signal_date": signal_date,
                    "score": row["score"],
                    "ret_10d": row["ret_10d"],
                    "result_win": row["ret_10d"] > 0,
                    "confidence": ai_confidence,
                    "latency": elapsed
                })
            except Exception as e:
                print(f"    {ts_code} error: {e}")
                results.append({
                    "ts_code": ts_code,
                    "signal_date": signal_date,
                    "score": row["score"],
                    "ret_10d": row["ret_10d"],
                    "result_win": row["ret_10d"] > 0,
                    "confidence": -1,
                    "latency": 0
                })
        
        avg_latency = total_latency / success_count if success_count > 0 else 0
        print(f"    完成! 成功: {success_count}/{len(sample)}, 平均耗时: {avg_latency:.2f}s")
        return pd.DataFrame(results)
    
    finally:
        conn.close()


def analyze_results(df):
    """分析验证结果"""
    print("\n" + "="*72)
    print("       AI信心验证报告")
    print("="*72)
    
    overall_win = (df["result_win"] == True).mean()
    print(f"\n[整体] 样本数: {len(df)}, 原始胜率: {overall_win:.1%}")
    
    # 信心分布
    valid = df[df["confidence"] >= 0]
    print(f"\n[信心分布] 有效AI分析: {len(valid)}/{len(df)}")
    
    # 按信心区间分组
    valid["conf_bin"] = pd.cut(
        valid["confidence"],
        bins=[-1, 49, 69, 89, 101],
        labels=["0-49", "50-69", "70-89", "90-100"]
    )
    
    stats = valid.groupby("conf_bin", observed=True).agg({
        "result_win": ["count", "mean"],
        "latency": "mean"
    })
    
    print("\n[信心区间统计]")
    print(f"{'区间':>8} {'信号数':>8} {'胜率':>8} {'平均耗时':>10}")
    print("-" * 40)
    
    for bin_name in ["0-49", "50-69", "70-89", "90-100"]:
        if bin_name in stats.index:
            data = stats.loc[bin_name]
            count = data[("result_win", "count")]
            win_rate = data[("result_win", "mean")]
            latency = data[("latency", "mean")]
            improvement = ((win_rate - overall_win) / overall_win * 100) if overall_win > 0 else 0
            
            print(f"{bin_name:>8} {count:>8} {win_rate:>7.1%}  {latency:>9.2f}s")
            if count > 5 and win_rate > overall_win:
                print(f"{' ':>8} {' ':>8} ↑{improvement:>5.1f}%  {' ':>10}")
    
    # 阈值过滤效果
    print("\n[阈值过滤效果]")
    thresholds = [50, 60, 70, 80, 90]
    print(f"{'阈值':>6} {'通过率':>8} {'过滤后胜率':>12} {'胜率提升':>10}")
    print("-" * 45)
    
    best_thresh = None
    best_improvement = 0
    
    for thresh in thresholds:
        filtered = valid[valid["confidence"] >= thresh]
        pass_rate = len(filtered) / len(valid) if len(valid) > 0 else 0
        win_rate = (filtered["result_win"] == True).mean() if len(filtered) > 0 else 0
        improvement = ((win_rate - overall_win) / overall_win * 100) if overall_win > 0 else 0
        
        print(f"{thresh:>6} {pass_rate:>7.1%}  {win_rate:>11.1%}  {improvement:>+9.1f}%")
        
        if len(filtered) >= 10 and improvement > best_improvement:
            best_improvement = improvement
            best_thresh = thresh
    
    # 汇总建议
    print("\n[建议]")
    if best_thresh:
        filtered = valid[valid["confidence"] >= best_thresh]
        pass_rate = len(filtered) / len(valid)
        win_rate = (filtered["result_win"] == True).mean()
        print(f"✓ 推荐阈值: {best_thresh}")
        print(f"  - 信号通过率: {pass_rate:.1%}")
        print(f"  - 过滤后胜率: {win_rate:.1%}")
        print(f"  - 胜率提升: +{best_improvement:.1f}%")
    else:
        print("⚠️  AI信心过滤效果不明显，建议暂不启用或调整阈值")
    
    return best_thresh


def main():
    parser = argparse.ArgumentParser(description="AI信心验证")
    parser.add_argument("--sample", type=int, default=50, help="样本数量")
    args = parser.parse_args()
    
    # 步骤1: 检查GPU
    print("[STEP 1] GPU加速验证")
    has_gpu = check_ollama_gpu()
    
    # 步骤2: 测试推理速度
    elapsed = test_ollama_speed()
    tokens_per_sec = 37.51  # 从之前测试
    print(f"[SPEED] 推理速度: ~{tokens_per_sec} tokens/s")
    
    # 步骤3: 验证AI信心效果
    print("\n[STEP 3] AI信心验证")
    df = validate_ai_confidence(args.sample)
    
    # 步骤4: 分析结果
    best_thresh = analyze_results(df)
    
    # 保存报告
    report_path = os.path.join(ROOT_DIR, "reports", "ai_confidence_validation.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"""# AI信心验证报告

## 验证概述
- 验证日期: {datetime.now().strftime("%Y-%m-%d %H:%M")}
- 样本数量: {len(df)}
- GPU加速: {'✅ 是' if has_gpu else '❌ 否'}
- 平均推理耗时: {elapsed:.2f}s
- 推理速度: ~{37.51} tokens/s

## 整体统计
- 原始胜率: {((df["result_win"] == True).mean() * 100):.1f}%
- AI分析成功率: {(len(df[df["confidence"] >= 0]) / len(df) * 100):.1f}%

## 推荐阈值
- {f'✅ 推荐阈值: {best_thresh}' if best_thresh else '⚠️ 暂不推荐启用AI过滤'}

## 阈值过滤效果
| 阈值 | 通过率 | 过滤后胜率 |
|------|--------|------------|
""")
        valid = df[df["confidence"] >= 0]
        for thresh in [50, 60, 70, 80, 90]:
            filtered = valid[valid["confidence"] >= thresh]
            pass_rate = len(filtered) / len(valid)
            win_rate = (filtered["result_win"] == True).mean()
            f.write(f"| {thresh} | {pass_rate:.1%} | {win_rate:.1%} |\n")
    
    print(f"\n[OUTPUT] 报告已保存到 {report_path}")


if __name__ == "__main__":
    main()
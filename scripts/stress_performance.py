# -*- coding: utf-8 -*-
"""
stress_performance.py —— 大模型并发、接口缓存与性能基准压测脚本
"""

import os
import sys
import time
import json
import requests
import threading
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLLAMA_API_GENERATE = "http://localhost:11434/api/generate"
OLLAMA_API_CHAT = "http://localhost:11434/api/chat"
OLLAMA_API_TAGS = "http://localhost:11434/api/tags"
WEB_SERVER_URL = "http://127.0.0.1:8000"

def get_available_model():
    """获取本地可用的Ollama模型"""
    try:
        resp = requests.get(OLLAMA_API_TAGS, timeout=5)
        if resp.status_code == 200:
            models = [m['name'] for m in resp.json().get('models', [])]
            # 优先选择 qwen2.5:7b 或其变体
            for m in models:
                if "qwen2.5:7b" in m:
                    return m
            for m in models:
                if "llama3" in m or "qwen" in m:
                    return m
            if models:
                return models[0]
    except Exception as e:
        print(f"警告：无法获取Ollama模型列表: {e}")
    return "qwen2.5:7b"

def unload_model(model_name):
    """卸载Ollama模型以模拟冷启动"""
    try:
        # 发送 keep_alive 为 0 的请求以卸载模型
        payload = {
            "model": model_name,
            "keep_alive": 0,
            "prompt": "",
            "stream": False
        }
        requests.post(OLLAMA_API_GENERATE, json=payload, timeout=10)
        # 等待一会确保释放完毕
        time.sleep(2)
        print(f"成功在Ollama中卸载模型 {model_name}，已重置为冷启动状态。")
        return True
    except Exception as e:
        print(f"警告：卸载模型失败: {e}")
        return False

def test_ollama_cold_start(model_name):
    """测试Ollama模型冷启动时间"""
    print(f"\n--- 开始 Ollama 冷启动测试 | 模型: {model_name} ---")
    unload_model(model_name)
    
    start_time = time.time()
    payload = {
        "model": model_name,
        "prompt": "你好，请用一句话自我介绍。",
        "stream": False
    }
    
    try:
        resp = requests.post(OLLAMA_API_GENERATE, json=payload, timeout=60)
        duration = time.time() - start_time
        if resp.status_code == 200:
            res_json = resp.json()
            print(f"冷启动请求成功，总耗时: {duration:.2f} 秒")
            # 尝试从 metrics 提取 load_duration
            # Ollama 的 metrics 有时候在返回的顶级字段中，有时候在其它地方，视版本而定
            # 比如 total_duration, load_duration (以纳秒表示)
            load_dur = res_json.get("load_duration", 0)
            if load_dur == 0 and "metrics" in res_json:
                load_dur = res_json["metrics"].get("load_duration", 0)
            
            if load_dur > 0:
                print(f"  模型加载时间(load_duration): {load_dur / 1e9:.2f} 秒")
            else:
                print(f"  模型加载时间: 未提供精确指标，估计为较大部分的总耗时。")
            return duration
        else:
            print(f"冷启动请求失败: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"冷启动测试异常: {e}")
    return None

def request_ollama_single(model_name, thread_id):
    """发起单次Ollama请求（带性能指标收集）"""
    payload = {
        "model": model_name,
        "messages": [
            {"role": "user", "content": f"请给出机器学习的简短定义，不超过30个字。测试ID是{thread_id}"}
        ],
        "stream": False
    }
    
    start_time = time.time()
    try:
        resp = requests.post(OLLAMA_API_CHAT, json=payload, timeout=90)
        duration = time.time() - start_time
        if resp.status_code == 200:
            res_json = resp.json()
            eval_count = res_json.get("eval_count", 0)
            eval_dur = res_json.get("eval_duration", 0)
            
            # 降级获取指标
            if eval_count == 0 and "metrics" in res_json:
                eval_count = res_json["metrics"].get("eval_count", 0)
                eval_dur = res_json["metrics"].get("eval_duration", 0)
                
            tokens_per_sec = 0.0
            if eval_count > 0 and eval_dur > 0:
                # eval_duration 是纳秒
                tokens_per_sec = eval_count / (eval_dur / 1e9)
            
            return {
                "success": True,
                "duration": duration,
                "tokens_per_sec": tokens_per_sec,
                "response": res_json.get("message", {}).get("content", "").strip()
            }
        else:
            return {"success": False, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def test_ollama_concurrency(model_name, concurrency=3):
    """压测Ollama并发性能"""
    print(f"\n--- 开始 Ollama 并发性能测试 | 并发数: {concurrency} | 模型: {model_name} ---")
    
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(request_ollama_single, model_name, i): i for i in range(concurrency)}
        for future in as_completed(futures):
            results.append(future.result())
            
    success_count = sum(1 for r in results if r.get("success", False))
    durations = [r["duration"] for r in results if r.get("success", False)]
    tps_list = [r["tokens_per_sec"] for r in results if r.get("success", False) and r.get("tokens_per_sec", 0.0) > 0]
    
    print(f"并发结果统计 ({success_count}/{concurrency} 成功):")
    if durations:
        print(f"  平均响应时间: {statistics.mean(durations):.2f} 秒")
        print(f"  最小响应时间: {min(durations):.2f} 秒")
        print(f"  最大响应时间: {max(durations):.2f} 秒")
    if tps_list:
        print(f"  平均推理速度: {statistics.mean(tps_list):.1f} tokens/秒")
    else:
        print(f"  无法获取推理速度指标")
        
    for i, r in enumerate(results):
        if not r["success"]:
            print(f"  请求 {i} 失败: {r.get('error')}")
            
    return {
        "concurrency": concurrency,
        "success_rate": success_count / concurrency * 100,
        "avg_duration": statistics.mean(durations) if durations else None,
        "avg_tps": statistics.mean(tps_list) if tps_list else None
    }

def request_stock_analysis(ts_code="600519.SH"):
    """请求个股分析接口"""
    url = f"{WEB_SERVER_URL}/api/stock_analysis"
    payload = {"ts_code": ts_code}
    
    start_time = time.time()
    try:
        resp = requests.post(url, json=payload, timeout=20)
        duration = time.time() - start_time
        if resp.status_code == 200:
            return {"success": True, "duration": duration}
        else:
            return {"success": False, "error": f"HTTP {resp.status_code} {resp.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def test_stock_analysis_cache(ts_code="600519.SH", repeats=20):
    """测试个股分析接口重复计算和缓存性能"""
    print(f"\n--- 开始个股诊股接口重复请求压测 | 股票: {ts_code} | 重复次数: {repeats} ---")
    
    durations = []
    success_count = 0
    
    for i in range(repeats):
        res = request_stock_analysis(ts_code)
        if res["success"]:
            success_count += 1
            durations.append(res["duration"])
            # 打印前几次和最后一次的耗时
            if i < 3 or i == repeats - 1:
                print(f"  第 {i+1:02d} 次请求耗时: {res['duration']:.4f} 秒")
            elif i == 3:
                print("  ...")
        else:
            print(f"  第 {i+1:02d} 次请求失败: {res.get('error')}")
            
    if durations:
        print(f"诊断分析统计:")
        print(f"  首次请求耗时: {durations[0]:.4f} 秒")
        if len(durations) > 1:
            avg_subsequent = statistics.mean(durations[1:])
            print(f"  后续请求平均耗时: {avg_subsequent:.4f} 秒")
            max_subsequent = max(durations[1:])
            min_subsequent = min(durations[1:])
            print(f"  后续请求范围: {min_subsequent:.4f} ~ {max_subsequent:.4f} 秒")
        print(f"  总平均耗时: {statistics.mean(durations):.4f} 秒")
        
    return durations

def test_api_baselines():
    """测试核心API的性能基准"""
    print("\n--- 开始系统核心API性能基准测试 ---")
    
    endpoints = [
        {"name": "系统状态查询 (/api/status)", "url": f"{WEB_SERVER_URL}/api/status", "method": "GET", "payload": None},
        {"name": "股票列表搜索 (/api/stocks)", "url": f"{WEB_SERVER_URL}/api/stocks", "method": "POST", "payload": {"ts_code": "600"}},
        {"name": "持仓组合获取 (/api/washout/portfolio)", "url": f"{WEB_SERVER_URL}/api/washout/portfolio", "method": "GET", "payload": None},
        {"name": "AI 配置获取 (/api/ai_config)", "url": f"{WEB_SERVER_URL}/api/ai_config", "method": "GET", "payload": None}
    ]
    
    for ep in endpoints:
        durations = []
        success = 0
        for _ in range(10):
            start = time.time()
            try:
                if ep["method"] == "GET":
                    r = requests.get(ep["url"], timeout=5)
                else:
                    r = requests.post(ep["url"], json=ep["payload"], timeout=5)
                
                if r.status_code == 200:
                    success += 1
                    durations.append(time.time() - start)
            except Exception:
                pass
                
        print(f"  {ep['name']}:")
        if durations:
            print(f"    成功率: {success/10*100:.0f}%, 平均耗时: {statistics.mean(durations)*1000:.1f}ms, 中位数: {statistics.median(durations)*1000:.1f}ms")
        else:
            print(f"    全部请求失败！")

def main():
    print("=" * 60)
    print("  StockAI Funnel 系统性能与大模型压测")
    print("=" * 60)
    
    # 0. 确认 web_server 是否存活
    try:
        r = requests.get(f"{WEB_SERVER_URL}/api/status", timeout=20)
        print(f"FastAPI Web Server 存活，状态码: {r.status_code}，开始测试。")
    except Exception as e:
        print(f"ERROR: 无法连接至 FastAPI Web Server ({WEB_SERVER_URL})。请先启动服务器。错误详情: {e}")
        sys.exit(1)
        
    # 1. 核心API基准测试
    test_api_baselines()
    
    # 2. 个股分析重复计算压测 (Baseline)
    test_stock_analysis_cache(ts_code="600519.SH", repeats=10)
    
    # 3. Ollama 测试
    model_name = get_available_model()
    print(f"\n检测到本地可用大模型: {model_name}")
    
    # 3.1 冷启动测试
    test_ollama_cold_start(model_name)
    
    # 3.2 并发测试 (3路并发)
    test_ollama_concurrency(model_name, concurrency=3)
    
    # 3.3 并发测试 (5路并发)
    test_ollama_concurrency(model_name, concurrency=5)

if __name__ == "__main__":
    main()

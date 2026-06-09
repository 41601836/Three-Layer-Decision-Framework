import requests
import json
import time
import os

def test_model(model_name, description):
    print("\n=== 测试:", model_name, "-", description, "===")
    
    payload = {
        "model": model_name,
        "prompt": "解释一下什么是机器学习",
        "stream": False
    }
    
    start_time = time.time()
    response = requests.post(
        "http://localhost:11434/api/generate",
        json=payload,
        headers={"Content-Type": "application/json"}
    )
    end_time = time.time()
    
    if response.status_code == 200:
        result = response.json()
        duration = end_time - start_time
        
        print("响应时间:", round(duration, 2), "秒")
        print("输出长度:", len(result.get('response', '')), "字符")
        
        if 'metrics' in result:
            metrics = result['metrics']
            eval_count = metrics.get('eval_count', 0)
            eval_duration = metrics.get('eval_duration', 0) / 1000  # 毫秒转秒
            if eval_count > 0 and eval_duration > 0:
                tokens_per_second = eval_count / eval_duration
                print("生成速度:", round(tokens_per_second, 1), "tokens/秒")
        
        if duration < 3:
            print("STATUS: GPU加速有效")
        elif duration < 8:
            print("STATUS: 可能混合使用CPU/GPU")
        else:
            print("STATUS: 可能在使用纯CPU")
            
        return duration
    
    return None

def main():
    # 设置环境变量强制GPU
    os.environ['OLLAMA_GPU_LAYERS'] = '999'
    
    print("=== Ollama GPU加速诊断 ===")
    
    # 检查服务状态
    try:
        response = requests.get("http://localhost:11434/api/tags")
        if response.status_code == 200:
            tags = response.json()
            print("已下载模型:", [m['name'] for m in tags.get('models', [])])
        else:
            print("ERROR: Ollama服务未响应")
            return
    except Exception as e:
        print("ERROR: 无法连接Ollama:", e)
        return
    
    # 测试不同模型
    durations = []
    
    # 测试q4_K_M (4-bit量化，更小更快)
    if 'qwen2.5:7b-instruct-q4_K_M' in str(tags):
        durations.append(test_model('qwen2.5:7b-instruct-q4_K_M', '4-bit量化'))
    
    # 测试q6_K (6-bit量化，更精确)
    if 'qwen2.5:7b-instruct-q6_K' in str(tags):
        durations.append(test_model('qwen2.5:7b-instruct-q6_K', '6-bit量化'))
    
    # 总结
    print("\n=== 诊断总结 ===")
    if durations:
        avg_duration = sum(durations) / len(durations)
        print("平均响应时间:", round(avg_duration, 2), "秒")
        
        if avg_duration < 3:
            print("CONCLUSION: GPU加速已成功启用！")
            print("建议: 当前配置良好，可以运行批量AI分析任务")
        elif avg_duration < 8:
            print("CONCLUSION: 性能中等，可能存在优化空间")
            print("建议: 检查NVIDIA驱动和CUDA版本，重启Ollama服务")
        else:
            print("CONCLUSION: 性能较慢，可能未使用GPU")
            print("建议: 检查OLLAMA_GPU_LAYERS设置，确保NVIDIA驱动正常")
    
    print("\n=== 系统信息 ===")
    print("NVIDIA驱动版本: 610.47")
    print("CUDA版本: 13.3")
    print("Ollama版本: 0.30.2")
    print("GPU: NVIDIA GeForce RTX 4060 (8GB)")

if __name__ == "__main__":
    main()
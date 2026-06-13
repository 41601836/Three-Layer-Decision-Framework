import requests
import json
import time
import os

def test_ollama_gpu():
    """测试Ollama是否使用GPU"""
    # 设置环境变量强制使用GPU
    os.environ['OLLAMA_GPU_LAYERS'] = '999'
    
    print("=== Ollama GPU加速测试 ===")
    
    # 检查Ollama服务状态
    try:
        response = requests.get("http://localhost:11434/api/tags")
        if response.status_code == 200:
            print("OK Ollama服务运行正常")
            tags = response.json()
            print("已下载模型:", [model['name'] for model in tags.get('models', [])])
        else:
            print("ERROR Ollama服务未运行")
            return
    except Exception as e:
        print("ERROR 无法连接Ollama服务:", e)
        return
    
    # 测试推理性能
    print("\n=== 推理性能测试 ===")
    start_time = time.time()
    
    payload = {
        "model": "qwen2.5:7b",
        "prompt": "你好",
        "stream": False
    }
    
    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 200:
            result = response.json()
            end_time = time.time()
            duration = end_time - start_time
            
            print("响应时间:", duration, "秒")
            print("输出内容:", result.get('response', '')[:50], "...")
            
            if 'metrics' in result:
                print("\n=== 推理指标 ===")
                metrics = result['metrics']
                if 'load_duration' in metrics:
                    print("  模型加载时间:", metrics['load_duration']/1000, "秒")
                if 'prompt_eval_count' in metrics:
                    print("  Prompt Token数:", metrics['prompt_eval_count'])
                if 'eval_count' in metrics:
                    print("  生成Token数:", metrics['eval_count'])
                if 'total_duration' in metrics:
                    print("  总耗时:", metrics['total_duration']/1000, "秒")
                if 'eval_duration' in metrics:
                    print("  推理耗时:", metrics['eval_duration']/1000, "秒")
            
            if duration < 2:
                print("\nGPU加速已启用！推理速度很快")
            elif duration < 5:
                print("\n可能在使用GPU，但速度一般")
            else:
                print("\n可能在使用CPU推理，速度较慢")
                
        else:
            print("请求失败:", response.status_code)
            print(response.text)
            
    except Exception as e:
        print("请求失败:", e)

if __name__ == "__main__":
    test_ollama_gpu()
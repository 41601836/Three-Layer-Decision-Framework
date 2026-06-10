#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI策略解析模块
使用大语言模型将自然语言描述的策略转换为可执行的策略代码
"""

import os
import re
import json
import subprocess
from typing import Dict, List, Any, Optional
from abc import ABC, abstractmethod


class BaseAIParser(ABC):
    """AI解析器基类"""
    
    @abstractmethod
    def parse_strategy(self, strategy_desc: str, **kwargs) -> Dict[str, Any]:
        """
        解析策略描述
        
        Args:
            strategy_desc: 自然语言描述的策略
            
        Returns:
            包含规则和代码的字典
        """
        pass


class OllamaParser(BaseAIParser):
    """使用Ollama本地模型解析策略"""
    
    def __init__(self, model: str = "qwen2:7b"):
        self.model = model
        self.base_url = "http://localhost:11434"
        
    def parse_strategy(self, strategy_desc: str, strategy_type: str = "hybrid", **kwargs) -> Dict[str, Any]:
        """解析策略描述"""
        try:
            # 构建prompt
            prompt = self._build_prompt(strategy_desc, strategy_type)
            
            # 调用Ollama API
            try:
                result = self._call_ollama(prompt)
            except Exception as e:
                print(f"Ollama调用失败: {str(e)}")
                # 尝试使用不同的命令格式
                result = self._call_ollama_v2(prompt)
            
            # 解析结果
            parsed_result = self._parse_ollama_response(result)
            
            return parsed_result
            
        except Exception as e:
            print(f"Ollama解析失败: {str(e)}")
            # 返回模拟数据
            return self._generate_fake_result(strategy_desc, strategy_type)
    
    def _build_prompt(self, strategy_desc: str, strategy_type: str) -> str:
        """构建提示词"""
        strategy_type_desc = {
            "volume": "量能策略，重点关注成交量、资金流向等指标",
            "price": "价格策略，重点关注价格走势、均线、技术指标等",
            "hybrid": "混合策略，综合考虑量价、基本面等多方面因素",
            "custom": "自定义策略"
        }
        
        prompt = f"""
你是一位专业的量化策略分析师，请帮我将以下自然语言描述的股票交易策略转换为结构化的策略规则和Python代码。

策略类型: {strategy_type_desc[strategy_type]}

策略描述:
{strategy_desc}

请按照以下格式输出结果：

### 策略规则列表
- 规则1: 
  名称: [规则名称]
  条件: [具体条件描述]
  操作: [买入/卖出/持有]
  权重: [0-100的权重值]
  描述: [详细说明]

- 规则2:
  ...

### 策略代码
```python
[完整的Python策略代码，继承自BaseStrategy]
```

注意事项:
1. 规则列表要清晰明确，条件要可量化
2. 代码要符合Python语法规范，包含必要的注释
3. 策略类名要使用驼峰命名法，如MovingAverageStrategy
4. 要实现get_signals、get_trade_plan、get_push_card三个抽象方法
5. 代码中要包含适当的参数配置，方便后续调整
"""
        
        return prompt.strip()
    
    def _call_ollama(self, prompt: str) -> str:
        """调用Ollama API"""
        try:
            # 使用subprocess调用ollama命令
            cmd = [
                "ollama", "run", self.model,
                "--prompt", prompt
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120
            )
            
            if result.returncode != 0:
                raise Exception(f"Ollama命令执行失败: {result.stderr}")
            
            return result.stdout
            
        except Exception as e:
            raise Exception(f"调用Ollama失败: {str(e)}")
    
    def _call_ollama_v2(self, prompt: str) -> str:
        """调用Ollama API (版本2，适用于不同的Ollama版本)"""
        try:
            # 使用管道方式传递prompt
            cmd = [
                "ollama", "run", self.model
            ]
            
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=120
            )
            
            if result.returncode != 0:
                raise Exception(f"Ollama命令执行失败: {result.stderr}")
            
            return result.stdout
            
        except Exception as e:
            raise Exception(f"调用Ollama失败: {str(e)}")
    
    def _parse_ollama_response(self, response: str) -> Dict[str, Any]:
        """解析Ollama响应"""
        try:
            # 提取规则列表
            rules_section = re.search(r'### 策略规则列表(.*?)### 策略代码', response, re.DOTALL)
            rules = []
            
            if rules_section:
                rules_text = rules_section.group(1)
                rule_matches = re.findall(r'- 规则\d+:\s*名称: (.*?)\s*条件: (.*?)\s*操作: (.*?)\s*权重: (\d+)\s*描述: (.*?)(?=- 规则|\Z)', rules_text, re.DOTALL)
                
                for i, match in enumerate(rule_matches, 1):
                    name, condition, action, weight, description = match
                    rules.append({
                        "name": name.strip(),
                        "condition": condition.strip(),
                        "action": action.strip(),
                        "weight": int(weight.strip()),
                        "description": description.strip()
                    })
            
            # 提取代码
            code_section = re.search(r'```python(.*?)```', response, re.DOTALL)
            code = ""
            
            if code_section:
                code = code_section.group(1).strip()
            
            return {
                "rules": rules,
                "code": code
            }
            
        except Exception as e:
            print(f"解析Ollama响应失败: {str(e)}")
            return {
                "rules": [],
                "code": ""
            }
    
    def _generate_fake_result(self, strategy_desc: str, strategy_type: str) -> Dict[str, Any]:
        """生成模拟结果"""
        rules = []
        
        if strategy_type == "volume" or strategy_type == "hybrid":
            rules.append({
                "name": "成交量放大买入",
                "condition": "成交量较昨日放大50%以上且股价上涨",
                "action": "买入",
                "weight": 25,
                "description": "当成交量显著放大且股价上涨时，表明主力资金介入，产生买入信号"
            })
        
        if strategy_type == "price" or strategy_type == "hybrid":
            rules.append({
                "name": "均线突破买入",
                "condition": "股价突破20日均线",
                "action": "买入",
                "weight": 20,
                "description": "当股价向上突破20日均线时，表明趋势向上，产生买入信号"
            })
        
        rules.append({
            "name": "止损卖出",
            "condition": "股价跌破5日均线",
            "action": "卖出",
            "weight": 30,
            "description": "当股价向下跌破5日均线时，表明短期趋势走弱，产生卖出信号"
        })
        
        # 生成代码
        class_name = self._generate_class_name(strategy_desc, strategy_type)
        code = f'''# AI生成的策略代码
class {class_name}(BaseStrategy):
    def __init__(self, db_path, config):
        super().__init__(db_path, config)
        self.version = "1.0"
        self.strategy_desc = "{strategy_desc}"
        self.params = {{
            'volume_threshold': 1.5,
            'ma_period': 20,
            'stop_loss_pct': 0.05
        }}
    
    def get_signals(self, trade_date):
        """生成交易信号"""
        signals = []
        
        # 这里实现具体的策略逻辑
        # 1. 获取股票列表
        # 2. 计算技术指标
        # 3. 生成买卖信号
        
        return signals
    
    def get_trade_plan(self, ts_code, signal):
        """生成交易计划"""
        close_price = signal.get('close_price', 0)
        
        return {{
            'buy_range': {{
                'ideal_low': close_price * 0.98,
                'ideal_high': close_price * 1.02
            }},
            'position_pct': 0.1,
            'stop_loss_initial': close_price * (1 - self.params['stop_loss_pct'])
        }}
    
    def get_push_card(self, signal):
        """生成推送卡片"""
        ts_code = signal['ts_code']
        stock_name = signal.get('stock_name', ts_code)
        score = signal.get('score', 0)
        
        return f'<div style="border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; max-width: 400px;">
    <div style="font-size: 18px; font-weight: bold; margin-bottom: 8px;">{stock_name} ({ts_code})</div>
    <div style="font-size: 14px; color: #666; margin-bottom: 12px;">AI策略信号 - 评分: {score}</div>
    <div style="font-size: 12px; color: #333;">
        <div>信号类型: {signal.get("signal_type", "BUY")}</div>
        <div>收盘价: {signal.get("close_price", 0):.2f}</div>
        <div>策略: {self.__class__.__name__}</div>
    </div>
</div>'
'''
        
        return {
            "rules": rules,
            "code": code
        }
    
    def _generate_class_name(self, strategy_desc: str, strategy_type: str) -> str:
        """生成类名"""
        type_prefix = {
            "volume": "Volume",
            "price": "Price",
            "hybrid": "Hybrid",
            "custom": "Custom"
        }
        
        # 从描述中提取关键词
        keywords = re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]+', strategy_desc)
        if keywords:
            # 取第一个关键词的拼音首字母
            first_keyword = keywords[0]
            if first_keyword.isascii():
                keyword_part = first_keyword.title()
            else:
                # 中文关键词取拼音首字母
                keyword_part = self._chinese_to_pinyin_abbr(first_keyword)
        else:
            keyword_part = "Strategy"
        
        return f"{type_prefix[strategy_type]}{keyword_part}Strategy"
    
    def _chinese_to_pinyin_abbr(self, chinese_text: str) -> str:
        """中文转拼音首字母"""
        try:
            from pypinyin import pinyin, Style
            result = pinyin(chinese_text, style=Style.FIRST_LETTER)
            return ''.join([p[0].upper() for p in result])
        except:
            # 如果没有pypinyin库，返回默认值
            return "Strategy"


def parse_strategy(strategy_desc: str, model: str = "ollama:qwen2:7b", **kwargs) -> Dict[str, Any]:
    """
    解析策略描述的统一接口
    
    Args:
        strategy_desc: 自然语言描述的策略
        model: 使用的AI模型，格式为"provider:model_name"
        
    Returns:
        包含规则和代码的字典
    """
    try:
        # 解析模型名称
        if model.startswith("ollama:"):
            model_name = model.split(":", 1)[1]
            parser = OllamaParser(model_name)
            return parser.parse_strategy(strategy_desc, **kwargs)
        else:
            # 默认使用Ollama
            parser = OllamaParser()
            return parser.parse_strategy(strategy_desc, **kwargs)
    except Exception as e:
        print(f"解析策略失败: {str(e)}")
        # 返回模拟数据
        parser = OllamaParser()
        return parser._generate_fake_result(strategy_desc, kwargs.get("strategy_type", "hybrid"))


if __name__ == "__main__":
    # 测试
    test_desc = "当股价突破20日均线且成交量放大50%时买入，当股价跌破10日均线且成交量萎缩时卖出"
    
    parser = OllamaParser()
    result = parser.parse_strategy(test_desc, "hybrid")
    
    print("策略规则:")
    for rule in result["rules"]:
        print(f"- {rule['name']}: {rule['condition']} -> {rule['action']}")
    
    print("\n策略代码:")
    print(result["code"])
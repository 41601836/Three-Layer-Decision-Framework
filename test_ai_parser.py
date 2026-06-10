#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试AI策略解析模块
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.ai_parser import parse_strategy, OllamaParser

def test_ai_parser():
    """测试AI解析模块"""
    print("=" * 60)
    print("📋 测试AI策略解析模块")
    print("=" * 60)
    
    # 测试策略描述
    strategy_desc = "当股价突破20日均线且成交量放大50%时买入，当股价跌破10日均线且成交量萎缩时卖出"
    
    print(f"\n📝 测试策略描述:\n{strategy_desc}")
    
    try:
        # 测试parse_strategy函数
        print("\n🔍 测试parse_strategy函数...")
        result = parse_strategy(strategy_desc)
        
        print("✅ AI解析模块加载成功")
        print(f"📋 规则数量: {len(result.get('rules', []))}")
        print(f"💻 代码生成: {'✅' if result.get('code') else '❌'}")
        
        if 'rules' in result and result['rules']:
            print("\n📋 生成的策略规则:")
            for i, rule in enumerate(result['rules'], 1):
                print(f"\n{i}. {rule['name']}")
                print(f"   条件: {rule['condition']}")
                print(f"   操作: {rule['action']}")
                print(f"   权重: {rule['weight']}")
                print(f"   描述: {rule['description']}")
        
        if 'code' in result and result['code']:
            print("\n💻 生成的策略代码预览:")
            lines = result['code'].split('\n')[:20]
            print('\n'.join(lines))
            if len(result['code'].split('\n')) > 20:
                print("... (显示前20行，完整代码共{}行)".format(len(result['code'].split('\n'))))
        
        # 测试不同策略类型
        print("\n" + "=" * 60)
        print("🔍 测试不同策略类型...")
        
        strategy_types = ['volume', 'price', 'hybrid', 'custom']
        
        for strategy_type in strategy_types:
            print(f"\n📊 策略类型: {strategy_type}")
            result = parse_strategy(strategy_desc, strategy_type=strategy_type)
            print(f"   规则数量: {len(result.get('rules', []))}")
            print(f"   代码生成: {'✅' if result.get('code') else '❌'}")
        
        # 测试OllamaParser
        print("\n" + "=" * 60)
        print("🔍 测试OllamaParser类...")
        
        parser = OllamaParser(model="qwen2:7b")
        result = parser.parse_strategy(strategy_desc, strategy_type="hybrid")
        
        print("✅ OllamaParser测试成功")
        print(f"📋 规则数量: {len(result.get('rules', []))}")
        print(f"💻 代码生成: {'✅' if result.get('code') else '❌'}")
        
        print("\n" + "=" * 60)
        print("🎉 AI策略解析模块测试完成!")
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = test_ai_parser()
    sys.exit(0 if success else 1)
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信息发送流程检验脚本
逐一检验飞书推送的每个环节
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_feishu_push_flow():
    print("=" * 60)
    print("     飞书推送流程检验")
    print("=" * 60)
    
    # 步骤1: 检查配置
    print("\n📋 步骤1: 配置读取")
    try:
        from config_loader import get_config
        webhook = get_config("api.feishu_webhook", "")
        print(f"   飞书 Webhook: {'✅ 已配置' if webhook and 'http' in webhook else '❌ 未配置或无效'}")
        if webhook and "在此填入" in webhook:
            print("   ⚠️ 警告: Webhook 仍为占位符，请配置真实URL")
    except Exception as e:
        print(f"   ❌ 配置读取失败: {e}")
        return False
    
    # 步骤2: 检查环境变量
    print("\n🌐 步骤2: 环境变量检查")
    env_webhook = os.environ.get("FEISHU_WEBHOOK", "")
    print(f"   FEISHU_WEBHOOK 环境变量: {'✅ 已设置' if env_webhook else '❌ 未设置'}")
    
    # 步骤3: 检查模块导入
    print("\n📦 步骤3: 模块导入测试")
    try:
        from scripts.feishu_bot import FEISHU_WEBHOOK, send_text, send_daily_brief, _post
        print("   ✅ feishu_bot 模块导入成功")
        print(f"   FEISHU_WEBHOOK 变量: {'已设置' if FEISHU_WEBHOOK else '为空'}")
    except Exception as e:
        print(f"   ❌ 模块导入失败: {e}")
        return False
    
    # 步骤4: 检查 FEISHU_WEBHOOK 配置
    print("\n🔗 步骤4: Webhook 配置检查")
    if not FEISHU_WEBHOOK:
        print("   ❌ FEISHU_WEBHOOK 未配置")
        print("   请选择以下方式之一配置:")
        print("   1. 修改 config.json 中的 api.feishu_webhook")
        print("   2. 设置 FEISHU_WEBHOOK 环境变量")
        print("   3. 创建 scripts/tokens.py 文件")
    else:
        print(f"   ✅ Webhook 已配置: {FEISHU_WEBHOOK[:50]}...")
    
    # 步骤5: 测试基本推送
    print("\n📤 步骤5: 基本推送测试")
    if FEISHU_WEBHOOK and "在此填入" not in FEISHU_WEBHOOK:
        try:
            result = send_text("📝 测试消息：飞书推送流程检验")
            print(f"   推送结果: {'✅ 成功' if result else '❌ 失败'}")
        except Exception as e:
            print(f"   ❌ 推送失败: {e}")
    else:
        print("   ⚠️ 跳过推送测试（Webhook 未配置或为占位符）")
    
    # 步骤6: 测试 send_daily_brief 函数
    print("\n📊 步骤6: send_daily_brief 函数测试")
    try:
        # 测试函数是否能正常调用（不实际发送）
        import inspect
        sig = inspect.signature(send_daily_brief)
        params = list(sig.parameters.keys())
        print(f"   ✅ 函数签名: send_daily_brief({', '.join(params)})")
        
        # 测试调用（使用默认参数）
        result = send_daily_brief(market_mode="测试", max_position=0.5)
        print(f"   ✅ 函数调用成功（结果取决于 Webhook 配置）")
    except Exception as e:
        print(f"   ❌ 函数测试失败: {e}")
    
    # 步骤7: 检查 _post 函数
    print("\n🔌 步骤7: HTTP 请求函数检查")
    try:
        # 检查 _post 函数是否存在
        import requests
        print("   ✅ requests 库可用")
        
        # 检查 _post 函数的实现
        import scripts.feishu_bot as feishu
        if hasattr(feishu, '_post'):
            print("   ✅ _post 函数存在")
        else:
            print("   ❌ _post 函数不存在")
    except ImportError:
        print("   ❌ requests 库未安装")
    
    # 步骤8: 检查日志配置
    print("\n📝 步骤8: 日志配置检查")
    try:
        import logging
        logger = logging.getLogger('scripts.feishu_bot')
        print(f"   ✅ 日志记录器存在")
        print(f"   日志级别: {logging.getLevelName(logger.level)}")
    except Exception as e:
        print(f"   ❌ 日志检查失败: {e}")
    
    print("\n" + "=" * 60)
    print("     流程检验完成")
    print("=" * 60)
    
    # 总结
    print("\n📋 检验总结:")
    if FEISHU_WEBHOOK and "在此填入" not in FEISHU_WEBHOOK:
        print("✅ 配置完整，可以发送飞书消息")
    else:
        print("❌ 配置不完整，请先配置飞书 Webhook")
        print("\n📌 配置方法:")
        print("1. 打开飞书群 -> 设置 -> 机器人 -> 添加自定义机器人")
        print("2. 复制 Webhook URL")
        print("3. 修改 config.json 中的 api.feishu_webhook")
        print("   或设置环境变量: set FEISHU_WEBHOOK=你的WebhookURL")
    
    return True

if __name__ == "__main__":
    test_feishu_push_flow()
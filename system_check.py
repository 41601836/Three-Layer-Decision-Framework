#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统状态检查
"""

import sys
import os

def main():
    print("✅ 系统状态检查")
    print(f"Python版本: {sys.version}")
    print(f"工作目录: {os.getcwd()}")
    
    # 检查核心模块
    modules = [
        ("策略管理器", "strategies.manager", "StrategyManager"),
        ("过滤引擎", "filter_engine", "FilterEngine"),
        ("AI解析模块", "strategies.ai_parser", "parse_strategy"),
        ("配置加载器", "config_loader", "load_config"),
        ("交易计划", "trade_plan", "generate_trade_plan"),
    ]
    
    all_success = True
    
    for module_name, import_path, class_name in modules:
        try:
            module = __import__(import_path, fromlist=[class_name])
            if hasattr(module, class_name):
                print(f"✅ {module_name} 加载成功")
            else:
                print(f"⚠️  {module_name} 加载成功，但未找到 {class_name}")
        except Exception as e:
            print(f"❌ {module_name} 加载失败: {e}")
            all_success = False
    
    # 检查策略
    print("\n📋 策略检查")
    try:
        from strategies.manager import StrategyManager
        manager = StrategyManager("db/stock_daily.db", {"enabled_strategies": []})
        available_strategies = manager.list_available_strategies()
        print(f"📊 可用策略: {available_strategies}")
    except Exception as e:
        print(f"❌ 策略检查失败: {e}")
        all_success = False
    
    # 检查数据库
    print("\n🗄️  数据库检查")
    db_path = "db/stock_daily.db"
    if os.path.exists(db_path):
        size = os.path.getsize(db_path) / (1024 * 1024)
        print(f"✅ 数据库文件存在，大小: {size:.2f} MB")
    else:
        print(f"⚠️  数据库文件不存在: {db_path}")
    
    # 检查配置文件
    print("\n⚙️  配置文件检查")
    config_files = ["config.json", "market_env.json", "threshold_config.py"]
    
    for config_file in config_files:
        if os.path.exists(config_file):
            print(f"✅ 配置文件存在: {config_file}")
        else:
            print(f"⚠️  配置文件不存在: {config_file}")
    
    print("\n" + "=" * 60)
    if all_success:
        print("🎉 系统状态良好，所有核心模块加载成功!")
    else:
        print("⚠️  系统存在部分问题，请检查上述错误信息")
    print("=" * 60)
    
    return 0 if all_success else 1

if __name__ == "__main__":
    sys.exit(main())
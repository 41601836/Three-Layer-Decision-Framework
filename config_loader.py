# -*- coding: utf-8 -*-
"""
config_loader.py —— 统一配置加载模块
=====================================

提供全局配置加载功能，支持从 config.json 读取配置。

使用方法：
    from config_loader import load_config, get_config
    
    # 加载配置
    config = load_config()
    
    # 获取配置项
    token = get_config("api.tushare_token")
    webhook = get_config("api.feishu_webhook")
    
    # 检查必填项
    validate_config()
"""

import os
import json
import logging

CONFIG_FILE = "config.json"
_config_cache = None

log = logging.getLogger(__name__)


def load_config() -> dict:
    """
    加载配置文件。
    
    返回：
        配置字典
    """
    global _config_cache
    
    if _config_cache is not None:
        return _config_cache
    
    if not os.path.exists(CONFIG_FILE):
        log.error(f"配置文件 {CONFIG_FILE} 不存在！")
        raise FileNotFoundError(f"配置文件 {CONFIG_FILE} 不存在，请创建并填写必要配置。")
    
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            _config_cache = json.load(f)
        log.info(f"配置文件加载成功: {CONFIG_FILE}")
        return _config_cache
    except json.JSONDecodeError as e:
        log.error(f"配置文件格式错误: {e}")
        raise


def get_config(key: str, default=None):
    """
    获取指定配置项，支持点分隔路径。
    
    参数：
        key: 配置键，支持点分隔（如 "api.tushare_token"）
        default: 默认值
    
    返回：
        配置值
    """
    config = load_config()
    
    try:
        keys = key.split(".")
        value = config
        for k in keys:
            value = value[k]
        return value
    except (KeyError, TypeError):
        if default is not None:
            return default
        log.warning(f"配置项 {key} 不存在")
        return None


def validate_config() -> bool:
    """
    验证必填配置项是否已填写。
    
    返回：
        True 表示验证通过，False 表示有缺失
    """
    config = load_config()
    
    missing = []
    
    # 检查 API 配置
    api = config.get("api", {})
    if not api.get("tushare_token") or api["tushare_token"] == "在此填入你的 Tushare Token":
        missing.append("api.tushare_token")
    if not api.get("feishu_webhook") or "你的机器人ID" in api["feishu_webhook"]:
        missing.append("api.feishu_webhook")
    
    if missing:
        print("\n" + "="*60)
        print("⚠️  配置验证失败")
        print("="*60)
        print("以下必填配置项未填写：")
        for item in missing:
            print(f"  - {item}")
        print("\n请编辑 config.json 文件，填写上述配置项后重新运行。")
        print("="*60 + "\n")
        return False
    
    log.info("配置验证通过")
    return True


def update_config(key: str, value) -> bool:
    """
    更新配置项并保存到文件。
    
    参数：
        key: 配置键，支持点分隔
        value: 新值
    
    返回：
        True 表示成功
    """
    global _config_cache
    
    config = load_config()
    
    keys = key.split(".")
    last_key = keys[-1]
    parent = config
    
    for k in keys[:-1]:
        if k not in parent:
            parent[k] = {}
        parent = parent[k]
    
    parent[last_key] = value
    
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
        
        _config_cache = config
        log.info(f"配置项 {key} 更新成功")
        return True
    except Exception as e:
        log.error(f"配置更新失败: {e}")
        return False


if __name__ == "__main__":
    import sys
    
    # 设置日志
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    
    try:
        config = load_config()
        print("\n当前配置概览:")
        print(f"策略名称: {config.get('strategy', {}).get('name', '未配置')}")
        print(f"策略版本: {config.get('strategy', {}).get('version', '未配置')}")
        print(f"Ollama模型: {config.get('ollama', {}).get('model', '未配置')}")
        print(f"Tushare Token: {'已配置' if config.get('api', {}).get('tushare_token') and '填入' not in config['api']['tushare_token'] else '未配置'}")
        print(f"飞书 Webhook: {'已配置' if config.get('api', {}).get('feishu_webhook') and '机器人ID' not in config['api']['feishu_webhook'] else '未配置'}")
        
        print("\n验证配置...")
        if validate_config():
            print("✅ 配置验证通过")
            sys.exit(0)
        else:
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ 配置加载失败: {e}")
        sys.exit(1)
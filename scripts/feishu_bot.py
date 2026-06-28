# -*- coding: utf-8 -*-
"""
feishu_bot.py —— 飞书机器人消息推送模块
"""

import os
import json
import requests


def send_text_message(content: str, webhook_url: str = None, secret: str = None):
    """发送文本消息到飞书群"""
    if not webhook_url:
        # 从配置文件读取
        try:
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                                      'deploy/config.json')
            with open(config_path) as f:
                config = json.load(f)
            webhook_url = config.get('feishu', {}).get('webhook_url', '')
            secret = config.get('feishu', {}).get('secret', '')
        except:
            print("⚠️ 未配置飞书 webhook_url")
            return False
    
    if not webhook_url:
        print("⚠️ 飞书 webhook_url 为空")
        return False
    
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    
    if secret:
        import time
        import hmac
        import hashlib
        timestamp = str(int(time.time()))
        sign = hmac.new(secret.encode('utf-8'), 
                        f"{timestamp}\n{secret}".encode('utf-8'), 
                        hashlib.sha256).hexdigest()
        url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"
    else:
        url = webhook_url
    
    data = {
        "msg_type": "text",
        "content": {
            "text": content
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        result = response.json()
        if result.get('code') == 0:
            print("✅ 飞书消息发送成功")
            return True
        else:
            print(f"❌ 飞书消息发送失败: {result.get('msg', '未知错误')}")
            return False
    except Exception as e:
        print(f"❌ 飞书消息发送异常: {e}")
        return False


def send_card_message(card: dict, webhook_url: str = None, secret: str = None):
    """发送卡片消息到飞书群"""
    if not webhook_url:
        try:
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                                      'deploy/config.json')
            with open(config_path) as f:
                config = json.load(f)
            webhook_url = config.get('feishu', {}).get('webhook_url', '')
            secret = config.get('feishu', {}).get('secret', '')
        except:
            print("⚠️ 未配置飞书 webhook_url")
            return False
    
    if not webhook_url:
        print("⚠️ 飞书 webhook_url 为空")
        return False
    
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    
    if secret:
        import time
        import hmac
        import hashlib
        timestamp = str(int(time.time()))
        sign = hmac.new(secret.encode('utf-8'), 
                        f"{timestamp}\n{secret}".encode('utf-8'), 
                        hashlib.sha256).hexdigest()
        url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"
    else:
        url = webhook_url
    
    data = {
        "msg_type": "interactive",
        "card": card
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        result = response.json()
        if result.get('code') == 0:
            print("✅ 飞书卡片消息发送成功")
            return True
        else:
            print(f"❌ 飞书卡片消息发送失败: {result.get('msg', '未知错误')}")
            return False
    except Exception as e:
        print(f"❌ 飞书卡片消息发送异常: {e}")
        return False


def build_signal_card(signals, strategy_version="v1.0_75pct", win_rate="75%"):
    """构建信号推送卡片"""
    if not signals:
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "📭 今日无信号"},
                "template": "red"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "plain_text", "content": "今日策略扫描未发现符合条件的信号"}
                },
                {
                    "tag": "div",
                    "text": {"tag": "plain_text", "content": f"\n📊 策略版本: {strategy_version}"},
                    "style": {"margin_top": "8px"}
                },
                {
                    "tag": "div",
                    "text": {"tag": "plain_text", "content": f"🎯 当前胜率: {win_rate}"},
                    "style": {"margin_top": "4px"}
                }
            ]
        }
    
    signal_items = []
    for i, sig in enumerate(signals[:5], 1):
        signal_items.extend([
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**{sig['code']}** {sig['name'] or ''}"},
                "style": {"margin_top": "8px" if i > 1 else "0px"}
            },
            {
                "tag": "div",
                "text": {"tag": "plain_text", "content": f"   评分: {sig['score']:.2f} | 价格: ¥{sig['price']:.2f} | 止损: ¥{sig['stop_loss']:.2f}"},
                "style": {"margin_top": "2px", "color": "#666666", "font_size": "12px"}
            }
        ])
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"📊 今日信号 ({len(signals)}个)"},
            "template": "blue"
        },
        "elements": [
            *signal_items,
            {
                "tag": "div",
                "text": {"tag": "plain_text", "content": f"\n📋 共 {len(signals)} 个信号（显示前5个）"},
                "style": {"margin_top": "12px", "color": "#999999", "font_size": "12px"}
            },
            {
                "tag": "hr",
                "style": {"margin_top": "12px", "margin_bottom": "12px"}
            },
            {
                "tag": "div",
                "text": {"tag": "plain_text", "content": f"📊 策略版本: {strategy_version}"},
                "style": {"color": "#666666", "font_size": "12px"}
            },
            {
                "tag": "div",
                "text": {"tag": "plain_text", "content": f"🎯 当前胜率: {win_rate}"},
                "style": {"margin_top": "4px", "color": "#666666", "font_size": "12px"}
            },
            {
                "tag": "div",
                "text": {"tag": "plain_text", "content": "💡 登录 http://localhost:5001 查看完整信号"},
                "style": {"margin_top": "4px", "color": "#1890ff", "font_size": "12px"}
            }
        ]
    }
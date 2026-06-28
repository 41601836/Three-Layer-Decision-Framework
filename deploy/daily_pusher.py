# -*- coding: utf-8 -*-
"""
daily_pusher.py —— 每日信号生成与推送脚本
生成今日策略信号并推送到飞书
"""

import os
import sys
import json
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from strategy.hunter import get_daily_signals


def generate_signal_report(signals):
    """生成信号报告内容"""
    if not signals:
        return {
            "title": "今日无信号",
            "text": "【策略信号】今日扫描未发现符合条件的信号\n\n策略版本: v1.0_75pct\n目标胜率: 70%\n当前胜率: 75%",
            "signals": []
        }
    
    signal_lines = []
    for i, sig in enumerate(signals[:10], 1):
        signal_lines.append(f"{i}. {sig['code']} {sig['name'] or ''}")
        signal_lines.append(f"   评分: {sig['score']:.2f}")
        signal_lines.append(f"   价格: {sig['price']:.2f}")
        signal_lines.append(f"   止损: {sig['stop_loss']:.2f}")
    
    title = f"今日信号 ({len(signals)}个)"
    text = "\n".join([
        "【策略信号】" + title,
        "-"*30,
        "\n".join(signal_lines),
        "",
        f"共 {len(signals)} 个信号（显示前10个）",
        "",
        "策略版本: v1.0_75pct",
        "目标胜率: 70%",
        "当前胜率: 75%",
        "",
        "提示: 登录 http://localhost:5001 查看完整信号"
    ])
    
    return {
        "title": title,
        "text": text,
        "signals": signals
    }


def push_to_feishu(report):
    """推送报告到飞书"""
    print("\n" + "="*60)
    print("📤 准备推送飞书消息")
    print("="*60)
    print(report['text'])
    print("="*60)
    
    try:
        from scripts.feishu_bot import send_text_message
        if send_text_message(report['text']):
            print("✅ 飞书文本消息发送成功")
        else:
            print("⚠️ 飞书文本消息发送失败")
    except ImportError:
        print("⚠️ 飞书机器人模块未配置，跳过实际推送")
    except Exception as e:
        print(f"⚠️ 飞书推送失败: {e}")
    
    print("="*60 + "\n")


def save_to_db(signals):
    """保存信号到数据库"""
    import sqlite3
    db_path = os.path.join(ROOT_DIR, 'db/strategy.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                score REAL,
                price REAL,
                stop_loss REAL,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        
        for sig in signals:
            cursor.execute("""
                INSERT OR REPLACE INTO signals 
                (date, code, name, score, price, stop_loss, status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """, (sig['date'], sig['code'], sig['name'] or '', 
                  sig['score'], sig['price'], sig['stop_loss']))
        
        conn.commit()
        print(f"✅ 已保存 {len(signals)} 个信号到数据库")
    finally:
        conn.close()


def main():
    date_str = datetime.now().strftime('%Y%m%d')
    print(f"🚀 开始 {date_str} 策略信号推送...")
    
    # ---- 新增：推送前强制更新数据 ----
    print("📥 正在拉取最新数据...")
    try:
        import subprocess
        fetch_script = os.path.join(ROOT_DIR, "scripts", "fetch_daily_batch.py")
        result = subprocess.run(
            [sys.executable, fetch_script],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            timeout=300
        )
        if result.returncode == 0:
            print("✅ 数据更新成功")
        else:
            print(f"⚠️ 数据更新失败: {result.stderr}")
    except Exception as e:
        print(f"⚠️ 数据更新异常: {e}")
    # ---- 新增结束 ----
    
    # 获取今日信号
    print("\n🔍 正在扫描策略信号...")
    signals = get_daily_signals()
    print(f"✅ 扫描完成，发现 {len(signals)} 个信号")
    
    # 生成报告
    report = generate_signal_report(signals)
    
    # 保存到数据库
    save_to_db(signals)
    
    # 推送飞书
    push_to_feishu(report)
    
    print("\n🎉 每日信号推送任务完成")
    return signals


if __name__ == "__main__":
    main()
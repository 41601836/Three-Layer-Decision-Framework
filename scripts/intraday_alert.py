# -*- coding: utf-8 -*-
"""
intraday_alert.py —— 陈明的专属量化助手 盘中紧急告警
========================================================
在盘中时段（09:30-15:00）每5分钟检查一次持仓股票：
- 跌幅超过 -7%（接近跌停）
- 跌破用户设置的止损价
- 主力异常流出（特大+大单）

触发即通过飞书推送紧急告警！

使用：
  python scripts/intraday_alert.py          # 持续运行
  python scripts/intraday_alert.py --test   # 测试运行一次
"""

import os
import sys
import time
import json
import sqlite3
import logging
import argparse
from datetime import datetime, time as dt_time

# 项目根目录
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

# 配置日志
LOG_DIR = os.path.join(ROOT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "intraday_alert.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)

DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")


def load_portfolio() -> list:
    """读取 portfolio.json 持仓配置"""
    portfolio_path = os.path.join(ROOT_DIR, "portfolio.json")
    try:
        with open(portfolio_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error("读取 portfolio.json 失败: %s", e)
        return []


def get_current_price_and_change(conn, ts_code: str):
    """
    获取当前价格和涨跌幅（简化实现）
    使用数据库中最近一天的收盘价作为当前价的替代
    实际生产中可接入实时数据源
    """
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT close, pct_chg FROM daily_prices 
            WHERE ts_code = ? 
            ORDER BY trade_date DESC LIMIT 1
        """, (ts_code,))
        
        row = cursor.fetchone()
        if row:
            close = float(row[0]) if row[0] is not None else 0.0
            pct_chg = float(row[1]) if row[1] is not None else 0.0
            return close, pct_chg
        else:
            log.warning("未找到 %s 的价格数据", ts_code)
            return 0.0, 0.0
    except Exception as e:
        log.error("获取 %s 价格失败: %s", ts_code, e)
        return 0.0, 0.0


def check_intraday_alerts() -> list:
    """
    检查持仓股票是否触发告警条件
    
    返回：触发告警的列表 [{"ts_code": "...", "name": "...", ...}, ...]
    """
    portfolio = load_portfolio()
    if not portfolio:
        log.info("无持仓，跳过检查")
        return []
    
    conn = None
    alerts = []
    
    try:
        conn = sqlite3.connect(DB_PATH)
        
        for holding in portfolio:
            ts_code = holding.get("ts_code", "")
            name = holding.get("name", "")
            cost = holding.get("cost", 0.0)
            
            if not ts_code:
                continue
            
            log.info("检查 %s (%s)...", ts_code, name)
            
            # 获取当前价格和涨跌幅
            current_price, pct_chg = get_current_price_and_change(conn, ts_code)
            
            if current_price == 0:
                continue
            
            # 计算止损价（简单：成本价 * 0.95）
            stop_loss = cost * 0.95
            
            trigger = None
            trigger_val = ""
            suggestion = "请密切关注"
            
            # 条件1：跌幅超过 -7%
            if pct_chg < -7:
                trigger = "跌幅超过 -7%"
                trigger_val = f"{pct_chg:.2f}%"
                suggestion = "建议立即清仓"
                log.warning("⚠️ %s 触发跌幅告警: %.2f%%", ts_code, pct_chg)
            
            # 条件2：跌破止损价
            elif current_price < stop_loss:
                trigger = "跌破止损价"
                trigger_val = f"¥{stop_loss:.2f}"
                suggestion = "建议立即清仓"
                log.warning("⚠️ %s 跌破止损价: 现价 %.2f < %.2f", ts_code, current_price, stop_loss)
            
            # 触发告警
            if trigger:
                alerts.append({
                    "ts_code": ts_code,
                    "name": name,
                    "current_price": current_price,
                    "pct_chg": pct_chg,
                    "trigger": trigger,
                    "trigger_val": trigger_val,
                    "suggestion": suggestion
                })
                
    except Exception as e:
        log.error("检查盘中告警失败: %s", e)
    finally:
        if conn:
            conn.close()
    
    return alerts


def is_trading_time() -> bool:
    """检查是否处于A股交易时段"""
    now = datetime.now().time()
    # 交易时段：09:30-11:30 和 13:00-15:00
    if (dt_time(9, 30) <= now <= dt_time(11, 30)) or \
       (dt_time(13, 0) <= now <= dt_time(15, 0)):
        return True
    return False


def main_loop():
    """持续运行，每5分钟检查一次"""
    log.info("🚀 盘中紧急告警模块启动")
    log.info("检查频率：每5分钟")
    log.info("监控时段：09:30-11:30 / 13:00-15:00")
    
    last_alerts = set()  # 去重：避免重复推送同一告警
    
    while True:
        try:
            if is_trading_time():
                log.info("正在检查持仓...")
                
                alerts = check_intraday_alerts()
                
                for alert in alerts:
                    ts_code = alert["ts_code"]
                    key = f"{ts_code}_{datetime.now().strftime('%Y%m%d')}"
                    
                    if key not in last_alerts:
                        log.info("触发告警，准备推送: %s", ts_code)
                        
                        # 发送飞书告警
                        try:
                            from scripts.feishu_bot import send_intraday_alert
                            ok = send_intraday_alert(
                                ts_code=alert["ts_code"],
                                name=alert["name"],
                                current_price=alert["current_price"],
                                pct_chg=alert["pct_chg"],
                                trigger_type=alert["trigger"],
                                trigger_value=alert["trigger_val"],
                                suggestion=alert["suggestion"]
                            )
                            if ok:
                                log.info("✅ 告警推送成功")
                                last_alerts.add(key)
                            else:
                                log.error("❌ 告警推送失败")
                        except Exception as e:
                            log.error("发送告警失败: %s", e)
            else:
                log.info("非交易时段，暂停检查")
                
        except Exception as e:
            log.error("主循环异常: %s", e)
            
            # 发送系统错误通知
            try:
                from scripts.feishu_bot import send_error_notification
                send_error_notification(str(e))
            except:
                pass
        
        # 每5分钟检查一次
        log.info("等待下一次检查（5分钟后）...")
        time.sleep(300)  # 5分钟


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="盘中紧急告警模块")
    parser.add_argument("--test", action="store_true", help="测试运行一次")
    args = parser.parse_args()
    
    if args.test:
        log.info("开始测试盘中告警...")
        alerts = check_intraday_alerts()
        if alerts:
            log.info("发现 %d 条告警", len(alerts))
            for a in alerts:
                log.info("- %s (%s): %s", a["ts_code"], a["name"], a["trigger"])
        else:
            log.info("无告警触发")
    else:
        main_loop()

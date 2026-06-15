# monday_warfare/tasks.py
import os
import logging
from datetime import datetime

logger = logging.getLogger("monday_wave")

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config_loader import get_config
from scripts.feishu_bot import send_text

from .strategy import run_strategy
from .fetch_data import run_full_update, fetch_weekly, fetch_moneyflow, fetch_margin, fetch_limits, fetch_fundamentals
from .db_setup import init_monday_tables

import os

def get_monday_wave_config():
    """获取周一战法配置"""
    return get_config("strategy.monday_wave", {
        "enable": True,
        "risk_profile": "稳健",
        "account_size": 150000,
        "pick_count": 3
    })

def is_enabled():
    """检查周一战法是否启用"""
    return get_monday_wave_config().get("enable", True)

def send_alert(title, message):
    """发送飞书告警/通知"""
    try:
        content = f"【周一战法】{title}\n\n{message}"
        send_text(content)
        logger.info(f"飞书推送成功: {title}")
    except Exception as e:
        logger.error(f"飞书推送失败: {e}")

def task_update_weekly_data():
    """更新周线数据任务"""
    if not is_enabled():
        logger.info("周一战法已禁用，跳过数据更新任务")
        return
    
    try:
        logger.info("开始执行周一战法数据更新任务")
        init_monday_tables()
        
        logger.info("更新周线数据...")
        fetch_weekly()
        
        logger.info("更新资金流向...")
        fetch_moneyflow()
        
        logger.info("更新融资余额...")
        fetch_margin()
        
        logger.info("更新涨跌停数据...")
        fetch_limits()
        
        logger.info("更新基本面数据...")
        fetch_fundamentals()
        
        send_alert("数据更新完成", "✅ 周线、资金流、基本面、融资、涨跌停数据已全部更新完成")
        logger.info("周一战法数据更新任务完成")
        
    except Exception as e:
        logger.error(f"周一战法数据更新任务失败: {e}", exc_info=True)
        send_alert("数据更新失败", f"❌ 数据更新任务执行失败\n\n错误信息: {str(e)}")

def task_pre_market_analysis():
    """盘前研判任务"""
    if not is_enabled():
        logger.info("周一战法已禁用，跳过敏前研判任务")
        return
    
    try:
        logger.info("开始执行盘前研判任务")
        config = get_monday_wave_config()
        
        result = run_strategy(
            risk_profile=config.get("risk_profile", "稳健"),
            num_stocks=config.get("pick_count", 3),
            account_size=config.get("account_size", 150000)
        )
        
        if result['status'] == 'skip':
            message = f"⚠️ 盘前研判 - {result['message']}"
        else:
            message = f"""📊 盘前研判报告
⏰ 数据日期: {result['last_date']}
📈 市场环境: {result['env_rating']}
🎯 建议仓位: {result['position_limit']*100:.0f}%
🔥 主线板块: {', '.join(result['top_sectors'][:3])}

精选池:
"""
            for i, pick in enumerate(result['picks'], 1):
                message += f"{i}. {pick['ts_code']} {pick['name']} | {pick['industry']} | ¥{pick['close']:.2f}\n"
        
        send_alert("盘前研判", message)
        logger.info("盘前研判任务完成")
        
    except Exception as e:
        logger.error(f"盘前研判任务失败: {e}", exc_info=True)
        send_alert("盘前研判失败", f"❌ 盘前研判任务执行失败\n\n错误信息: {str(e)}")

def task_morning_scan():
    """早盘初筛任务"""
    if not is_enabled():
        logger.info("周一战法已禁用，跳过早盘初筛任务")
        return
    
    try:
        logger.info("开始执行早盘初筛任务")
        config = get_monday_wave_config()
        
        result = run_strategy(
            risk_profile=config.get("risk_profile", "稳健"),
            num_stocks=config.get("pick_count", 3),
            account_size=config.get("account_size", 150000)
        )
        
        if result['status'] == 'skip':
            message = f"⚠️ 早盘初筛 - {result['message']}"
        else:
            message = f"""☀️ 早盘初筛报告
⏰ 数据日期: {result['last_date']}
📈 市场环境: {result['env_rating']}
🎯 建议仓位: {result['position_limit']*100:.0f}%

精选池:
"""
            for i, pick in enumerate(result['picks'], 1):
                message += f"{i}. {pick['ts_code']} {pick['name']} | {pick['industry']} | ¥{pick['close']:.2f}\n"
        
        send_alert("早盘初筛", message)
        logger.info("早盘初筛任务完成")
        
    except Exception as e:
        logger.error(f"早盘初筛任务失败: {e}", exc_info=True)
        send_alert("早盘初筛失败", f"❌ 早盘初筛任务执行失败\n\n错误信息: {str(e)}")

def task_close_scan():
    """尾盘精选任务"""
    if not is_enabled():
        logger.info("周一战法已禁用，跳过尾盘精选任务")
        return
    
    try:
        logger.info("开始执行尾盘精选任务")
        config = get_monday_wave_config()
        
        result = run_strategy(
            risk_profile=config.get("risk_profile", "稳健"),
            num_stocks=config.get("pick_count", 3),
            account_size=config.get("account_size", 150000)
        )
        
        if result['status'] == 'skip':
            message = f"⚠️ 尾盘精选 - {result['message']}"
        else:
            message = f"""🌙 尾盘精选报告
⏰ 数据日期: {result['last_date']}
📈 市场环境: {result['env_rating']}
🎯 建议仓位: {result['position_limit']*100:.0f}%
🔥 主线板块: {', '.join(result['top_sectors'][:3])}

今日精选:
"""
            for i, pick in enumerate(result['picks'], 1):
                message += f"{i}. {pick['ts_code']} {pick['name']}\n   └─ 行业: {pick['industry']} | 价格: ¥{pick['close']:.2f}\n"
        
        send_alert("尾盘精选", message)
        logger.info("尾盘精选任务完成")
        
    except Exception as e:
        logger.error(f"尾盘精选任务失败: {e}", exc_info=True)
        send_alert("尾盘精选失败", f"❌ 尾盘精选任务执行失败\n\n错误信息: {str(e)}")

def task_friday_clear_remind():
    """周五清仓提醒任务"""
    if not is_enabled():
        logger.info("周一战法已禁用，跳过周五清仓提醒任务")
        return
    
    try:
        logger.info("执行周五清仓提醒任务")
        today = datetime.now().strftime('%Y-%m-%d')
        
        message = f"""🔔 周五清仓提醒

📅 今日日期: {today}

根据周一战法规则，周五建议：
1. ✅ 检查持仓，设置止损
2. ⚠️ 避免新开仓
3. 📉 建议减仓或清仓，规避周末风险

祝周末愉快！"""
        
        send_alert("周五清仓提醒", message)
        logger.info("周五清仓提醒任务完成")
        
    except Exception as e:
        logger.error(f"周五清仓提醒任务失败: {e}", exc_info=True)
        send_alert("周五清仓提醒失败", f"❌ 周五清仓提醒任务执行失败\n\n错误信息: {str(e)}")

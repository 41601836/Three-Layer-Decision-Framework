#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据验证与自动更新模块
========================
- 检查日线数据和指数数据是否为最新
- 自动调用更新脚本拉取最新数据
- 支持强制更新模式
"""
import os
import sys
import time
import sqlite3
import subprocess
from datetime import datetime, time as dt_time, timedelta

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")

# 强制更新时间（每日19:00）
FORCE_UPDATE_HOUR = 19
FORCE_UPDATE_MINUTE = 0

# 允许的最大数据延迟天数
MAX_ALLOWED_DELAY_DAYS = 1


def _get_latest_dates(conn: sqlite3.Connection) -> tuple:
    """获取数据库中最新的日线和指数数据日期"""
    try:
        row = conn.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
        latest_daily = row[0] if row and row[0] else None
        
        row = conn.execute("SELECT MAX(trade_date) FROM daily_index").fetchone()
        latest_index = row[0] if row and row[0] else None
        
        return latest_daily, latest_index
    except Exception as e:
        print(f"[ERROR] 查询最新日期失败: {e}")
        return None, None


def _get_expected_date() -> str:
    """计算期望的最新数据日期（考虑非交易日）"""
    today = datetime.now()
    
    # 检查是否在交易时间后（15:30之后）
    if today.time() >= dt_time(15, 30):
        # 交易时间后，期望日期为今日
        return today.strftime("%Y%m%d")
    else:
        # 交易时间前，期望日期为昨日
        yesterday = today - timedelta(days=1)
        return yesterday.strftime("%Y%m%d")


def _is_data_latest(latest_date: str, expected_date: str) -> bool:
    """判断数据是否为最新"""
    if not latest_date:
        return False
    return latest_date >= expected_date


def _run_fetch_script(script_name: str) -> bool:
    """运行数据获取脚本"""
    script_path = os.path.join(ROOT_DIR, "scripts", script_name)
    if not os.path.exists(script_path):
        print(f"[ERROR] 脚本不存在: {script_path}")
        return False
    
    print(f"[INFO] 正在运行 {script_name}...")
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            cwd=ROOT_DIR,
            capture_output=True,
            text=False,  # 使用bytes模式避免编码问题
            timeout=300
        )
        # 手动处理UTF-8解码
        if result.stdout:
            try:
                stdout_str = result.stdout.decode('utf-8', errors='replace')
                print(f"[INFO] {script_name} 输出:\n{stdout_str}")
            except:
                print(f"[INFO] {script_name} 输出: (二进制数据无法解码)")
        if result.stderr:
            try:
                stderr_str = result.stderr.decode('utf-8', errors='replace')
                print(f"[WARNING] {script_name} 错误输出:\n{stderr_str}")
            except:
                print(f"[WARNING] {script_name} 错误输出: (二进制数据无法解码)")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"[ERROR] {script_name} 执行超时")
        return False
    except Exception as e:
        print(f"[ERROR] 运行 {script_name} 失败: {e}")
        return False


def _should_force_update() -> bool:
    """判断是否到达强制更新时间（每日19:00）"""
    now = datetime.now()
    current_hour = now.hour
    current_minute = now.minute
    
    # 检查是否在19:00 ± 5分钟范围内
    if current_hour == FORCE_UPDATE_HOUR:
        if abs(current_minute - FORCE_UPDATE_MINUTE) <= 5:
            return True
    return False


def validate_and_update_data(force_update: bool = False) -> bool:
    """
    验证数据是否最新，若滞后则自动更新。
    
    参数：
        force_update: 是否强制更新（默认False，会检查强制更新时间）
    
    返回：
        True: 数据验证通过（已最新或更新成功）
        False: 数据更新失败
    """
    print("\n" + "=" * 60)
    print("  StockAI 数据验证与自动更新模块")
    print("=" * 60)
    
    # 检查是否到达强制更新时间
    if not force_update and _should_force_update():
        print(f"[INFO] 到达每日强制更新时间（{FORCE_UPDATE_HOUR}:{FORCE_UPDATE_MINUTE:02d}）")
        force_update = True
    
    # 连接数据库
    try:
        conn = sqlite3.connect(DB_PATH)
    except Exception as e:
        print(f"[ERROR] 数据库连接失败: {e}")
        return False
    
    try:
        # 获取当前数据日期
        latest_daily, latest_index = _get_latest_dates(conn)
        expected_date = _get_expected_date()
        
        print(f"[INFO] 期望最新日期: {expected_date}")
        print(f"[INFO] 日线数据最新日期: {latest_daily or '无数据'}")
        print(f"[INFO] 指数数据最新日期: {latest_index or '无数据'}")
        
        # 判断是否需要更新
        daily_ok = _is_data_latest(latest_daily, expected_date)
        index_ok = _is_data_latest(latest_index, expected_date)
        
        if daily_ok and index_ok and not force_update:
            print("[OK] 所有数据均为最新，无需更新")
            return True
        
        if force_update:
            print("[INFO] 执行强制数据更新...")
        else:
            print("[WARNING] 数据滞后，需要更新")
        
        # 步骤1：更新日线数据
        if not daily_ok or force_update:
            print("\n[STEP 1/2] 更新日线数据...")
            if not _run_fetch_script("fetch_daily.py"):
                print("[ERROR] 日线数据更新失败")
                return False
        
        # 步骤2：更新指数数据
        if not index_ok or force_update:
            print("\n[STEP 2/2] 更新指数数据...")
            if not _run_fetch_script("fetch_index.py"):
                print("[ERROR] 指数数据更新失败")
                return False
        
        # 验证更新结果
        time.sleep(2)  # 等待写入完成
        latest_daily_new, latest_index_new = _get_latest_dates(conn)
        
        daily_ok_new = _is_data_latest(latest_daily_new, expected_date)
        index_ok_new = _is_data_latest(latest_index_new, expected_date)
        
        if daily_ok_new and index_ok_new:
            print("\n[OK] 数据更新完成，所有数据均为最新")
            return True
        else:
            print(f"[ERROR] 数据更新后仍未达到最新")
            print(f"       日线最新: {latest_daily_new}, 指数最新: {latest_index_new}")
            return False
            
    finally:
        conn.close()
    
    return False


def check_data_health() -> dict:
    """
    检查数据健康状态，返回详细信息。
    
    返回：
        dict: 包含各数据类型的健康状态
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        latest_daily, latest_index = _get_latest_dates(conn)
        conn.close()
        
        expected_date = _get_expected_date()
        
        return {
            "expected_date": expected_date,
            "daily_prices_latest": latest_daily,
            "daily_index_latest": latest_index,
            "daily_prices_ok": _is_data_latest(latest_daily, expected_date),
            "daily_index_ok": _is_data_latest(latest_index, expected_date),
            "overall_ok": _is_data_latest(latest_daily, expected_date) and 
                         _is_data_latest(latest_index, expected_date)
        }
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="StockAI 数据验证与更新")
    parser.add_argument("-f", "--force", action="store_true", help="强制更新数据")
    parser.add_argument("-c", "--check", action="store_true", help="仅检查状态")
    args = parser.parse_args()
    
    if args.check:
        health = check_data_health()
        print("\n" + "=" * 60)
        print("  数据健康检查报告")
        print("=" * 60)
        print(f"期望日期: {health.get('expected_date', 'N/A')}")
        print(f"日线数据: {health.get('daily_prices_latest', 'N/A')} "
              f"→ {'✅' if health.get('daily_prices_ok') else '❌'}")
        print(f"指数数据: {health.get('daily_index_latest', 'N/A')} "
              f"→ {'✅' if health.get('daily_index_ok') else '❌'}")
        print(f"\n整体状态: {'✅ 健康' if health.get('overall_ok') else '❌ 需要更新'}")
    else:
        success = validate_and_update_data(force_update=args.force)
        sys.exit(0 if success else 1)
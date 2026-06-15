#!/usr/bin/env python3
# 测试性能监控器修复
import sys
sys.path.insert(0, 'G:/windows stock ai/three-layer-decision-framework')

from config.performance_monitor import PerformanceMonitor, PerformanceTimer
from datetime import datetime, time

# 创建监控器
monitor = PerformanceMonitor()

# 测试1: 尝试拉取当天数据（在交易时间前）
today = datetime.now()
today_str = today.strftime("%Y%m%d")

print(f"当前时间: {today}")
print(f"日期: {today_str}")
print(f"是否在交易时间前: {today.time() < time(15, 30)}")

# 模拟当天数据拉取（无数据）
with PerformanceTimer(monitor, 'daily_fetch_test', metadata={'date': today_str}) as timer:
    # 模拟未获取到数据（交易时间前）
    timer.add_records(0)

print(f"\n告警数量: {len(monitor.alerts)}")
for alert in monitor.alerts:
    print(f"  [{alert.level.value.upper()}] {alert.message}")

if len(monitor.alerts) == 0:
    print("✓ 测试通过！当天数据未生成时未触发误告警")
else:
    print("✗ 测试失败！仍有告警触发")

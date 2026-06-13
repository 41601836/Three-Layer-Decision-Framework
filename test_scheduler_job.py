# -*- coding: utf-8 -*-
# test_scheduler_job.py
from scheduler.extend_jobs.macro_jobs import (
    job_daily_before_trade,
    job_intraday_snapshot_1030
)
from utils.logger import scheduler_log

def main():
    print("===== 手动触发定时任务测试 =====")
    # 测试盘前任务
    job_daily_before_trade()
    print("\n--------------------------------")
    # 测试10:30快照任务
    job_intraday_snapshot_1030()
    print("===== 测试结束，请查看日志与数据库 =====")

if __name__ == "__main__":
    main()

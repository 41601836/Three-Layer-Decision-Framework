# -*- coding: utf-8 -*-
"""
logger.py —— 数据采集日志模块
============================

配置项目全局使用的数据采集日志器 collect_log，支持输出到控制台与本地日志文件。
"""

import logging
import os

# 默认日志目录
LOG_DIR = "logs"
# 默认日志文件名
LOG_FILE = "collect.log"
# 默认日志格式
LOG_FORMAT = "%(asctime)s [%(levelname)s] (%(name)s) %(message)s"

# 确保日志存储目录存在
os.makedirs(LOG_DIR, exist_ok=True)

# 初始化全局数据采集日志器
collect_log = logging.getLogger("collect")

if not collect_log.handlers:
    collect_log.setLevel(logging.INFO)
    formatter = logging.Formatter(LOG_FORMAT)
    
    # 1. 控制台输出 Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    collect_log.addHandler(console_handler)
    
    # 2. 本地文件输出 Handler
    log_path = os.path.join(LOG_DIR, LOG_FILE)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    collect_log.addHandler(file_handler)

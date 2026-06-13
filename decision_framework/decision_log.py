# -*- coding: utf-8 -*-
"""
decision_log.py —— 决策专属日志器
"""
import logging
import os

LOG_DIR = "logs"
LOG_FILE = "decision.log"
LOG_FORMAT = "%(asctime)s [%(levelname)s] (%(name)s) %(message)s"

os.makedirs(LOG_DIR, exist_ok=True)

decision_log = logging.getLogger("decision")

if not decision_log.handlers:
    decision_log.setLevel(logging.INFO)
    formatter = logging.Formatter(LOG_FORMAT)
    
    # 1. 控制台输出 Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    decision_log.addHandler(console_handler)
    
    # 2. 本地文件输出 Handler
    log_path = os.path.join(LOG_DIR, LOG_FILE)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    decision_log.addHandler(file_handler)

# -*- coding: utf-8 -*-
"""
StockAI_Funnel 全局配置
"""

import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 第一层：Python 硬过滤配置 ────────────────────────────────────────────────
FILTER_CONFIG = {
    "lookback_days":        60,
    "min_data_points":      20,
    "min_amount_tushare":   50000,   # 5000万元（Tushare amount 单位为千元）
    "max_amplitude_veto":   0.50,
    "macd_fast":            12,
    "macd_slow":            26,
    "macd_signal":          9,
    "rsi_period":           14,
    "rsi_oversold":         30,
    "rsi_overbought":       75,
    "ma_short":             5,
    "ma_mid":               20,
    "ma_long":              60,
    "top_n":                50,
    "output_dir":           os.path.join(ROOT_DIR, "data", "filter_results"),
}

# ── 第二层：AI 深度分析配置 ──────────────────────────────────────────────────
AI_CONFIG = {
    "model":          "qwen2.5:7b-instruct-q4_K_M",
    "ollama_api":     "http://localhost:11434/api/chat",
    "max_concurrent": 3,       # 并发数上限，防止爆显存
    "timeout_conn":   10,      # 连接超时（秒）
    "timeout_read":   120,     # 读取超时（秒）
    "trigger_score":  40,      # Python 分低于此值不触发 AI
    "output_dir":     os.path.join(ROOT_DIR, "data", "ai_results"),
}

# ── 第三层：飞书推送配置 ─────────────────────────────────────────────────────
FEISHU_CONFIG = {
    "max_items_per_message": 10,
    "title":                 "StockAI_Funnel 三层漏斗选股播报",
}

# ── 数据库 ───────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")
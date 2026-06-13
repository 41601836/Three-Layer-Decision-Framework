# -*- coding: utf-8 -*-
"""
check_files.py —— 核对 StockAI Funnel 架构与核心文件完整性
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

FILES_TO_CHECK = [
    "db/stock_daily.db",
    "scripts/fetch_daily.py",
    "filter_engine.py",
    "analyze_stock.py",
    "industry_strength.py",
    "washout_analyst.py",
    "scripts/scheduler.py",
    "web_server.py",
    "portfolio.json",
    "backtest_real_results.csv",
    "static/index.html",
    "static/macro.html",
    "static/market.html",
    "static/backtest.html",
    "static/portfolio.html",
]

def main():
    print("=" * 60)
    print("  开始进行 StockAI Funnel 架构核心文件核查")
    print("=" * 60)
    
    missing_count = 0
    for file_rel in FILES_TO_CHECK:
        full_path = os.path.join(ROOT_DIR, file_rel)
        exists = os.path.exists(full_path)
        status = "✅ 存在" if exists else "❌ 缺失"
        size_str = f"({os.path.getsize(full_path)} 字节)" if exists else ""
        print(f"  - {file_rel:<35} : {status} {size_str}")
        if not exists:
            missing_count += 1
            
    print("-" * 60)
    if missing_count == 0:
        print("🎉 完整性核查通过！所有核心架构文件均完整存在。")
        sys.exit(0)
    else:
        print(f"❌ 核查未通过！共有 {missing_count} 个核心文件缺失。")
        sys.exit(1)

if __name__ == "__main__":
    main()

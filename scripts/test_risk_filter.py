# -*- coding: utf-8 -*-
"""
test_risk_filter.py —— 验证 filter_engine 风控和一票否决逻辑是否正常工作
"""

import os
import sys
import sqlite3
import pandas as pd
from collections import namedtuple

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from filter_engine import score_one, FILTER_CONFIG

def test_filters():
    print("=" * 60)
    print("  开始验证 FilterEngine 一票否决风控规则")
    print("=" * 60)
    
    conn = sqlite3.connect("db/stock_daily.db")
    
    # 模拟一个符合 namedtuple 格式的 row 对象
    Row = namedtuple("Row", ["ts_code", "name", "industry", "area", "list_date", "circ_mv"])
    
    # 我们以 000001.SZ 为基准测试，但修改其基础属性来触发否决
    base_stock_code = "000001.SZ"
    
    # 1. 正常对照组
    row_normal = Row(base_stock_code, "平安银行", "银行", "深圳", "19910403", 2000000.0) # 市值 2000 亿
    r_normal = score_one(base_stock_code, row_normal.name, row_normal.industry, conn, row=row_normal)
    print(f"  - 正常对照组: {'🟢 通过' if r_normal is not None else '❌ 否决'}")
    
    # 2. ST股否决测试
    row_st = Row(base_stock_code, "*ST平安", "银行", "深圳", "19910403", 2000000.0)
    r_st = score_one(base_stock_code, row_st.name, row_st.industry, conn, row=row_st)
    print(f"  - ST股过滤测试: {'❌ 未拦截' if r_st is not None else '✅ 成功拦截 (ST股限制)'}")
    
    # 3. 微盘股过滤测试（流通市值 < 10 亿，在 config 里设置 10亿 = 100000 万元）
    row_micro = Row(base_stock_code, "平安银行", "银行", "深圳", "19910403", 50000.0) # 市值 5 亿
    r_micro = score_one(base_stock_code, row_micro.name, row_micro.industry, conn, row=row_micro)
    print(f"  - 微盘股过滤测试: {'❌ 未拦截' if r_micro is not None else '✅ 成功拦截 (市值低于10亿)'}")
    
    # 4. 成交额过低过滤测试
    # 修改 config 把成交额门槛提到极高，模拟当日交易极其清淡
    cfg_low_volume = FILTER_CONFIG.copy()
    cfg_low_volume["min_amount_tushare"] = 99999999 # 设置极高门槛
    r_low_vol = score_one(base_stock_code, row_normal.name, row_normal.industry, conn, cfg=cfg_low_volume, row=row_normal)
    print(f"  - 成交额低过滤测试: {'❌ 未拦截' if r_low_vol is not None else '✅ 成功拦截 (成交额不足)'}")
    
    conn.close()
    print("-" * 60)
    
    if r_normal is not None and r_st is None and r_micro is None and r_low_vol is None:
        print("🎉 风控与过滤规则验证全部通过！拦截策略符合设计预期。")
        sys.exit(0)
    else:
        print("❌ 风控规则拦截验证失败！请检查 filter_engine.py 的逻辑。")
        sys.exit(1)

if __name__ == "__main__":
    test_filters()

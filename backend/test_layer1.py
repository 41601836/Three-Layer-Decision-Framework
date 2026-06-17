# -*- coding: utf-8 -*-
"""
Phase 1 集成测试脚本
验证：宏观抓取、六维评分、一票否决、完整诊断流程
"""
import sys
import os
import logging

# 确保 backend 在路径里
BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'backend')
sys.path.insert(0, os.path.abspath(BACKEND_DIR))

logging.basicConfig(level=logging.WARNING)  # 测试时只显示警告

def separator(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)

# ─── Test 1: 宏观抓取 ─────────────────────────────────────────────────────────
separator("Test 1: 宏观数据采集 fetch_all_macro()")
try:
    from app.services.macro_fetcher import fetch_all_macro
    result = fetch_all_macro()
    print("海外宏观:", result['overseas'])
    print("国内宏观:", result['domestic'])
    print("状态:", result['status'])
    print("✅ PASS")
except Exception as e:
    print(f"❌ FAIL: {e}")

# ─── Test 2: 资金数据 ─────────────────────────────────────────────────────────
separator("Test 2: 资金结构 get_funds_data()")
try:
    from app.services.layer1_engine import get_funds_data
    funds = get_funds_data()
    print(funds)
    assert isinstance(funds, dict)
    print("✅ PASS")
except Exception as e:
    print(f"❌ FAIL: {e}")

# ─── Test 3: 情绪数据 ─────────────────────────────────────────────────────────
separator("Test 3: 情绪温度 get_sentiment_data()")
try:
    from app.services.layer1_engine import get_sentiment_data
    sent = get_sentiment_data()
    print(sent)
    assert isinstance(sent, dict)
    print("✅ PASS")
except Exception as e:
    print(f"❌ FAIL: {e}")

# ─── Test 4: 指数位置 ─────────────────────────────────────────────────────────
separator("Test 4: 指数位置 get_index_position()")
try:
    from app.services.layer1_engine import get_index_position
    idx = get_index_position()
    print(idx)
    assert isinstance(idx, dict)
    print("✅ PASS")
except Exception as e:
    print(f"❌ FAIL: {e}")

# ─── Test 5: 一票否决模拟 ─────────────────────────────────────────────────────
separator("Test 5: 一票否决 check_veto() - 模拟极端数据")
try:
    from app.services.layer1_engine import check_veto
    # 模拟：流动性枯竭
    veto = check_veto(
        funds_data={'total_amount': 5000, 'north_net_inflow': -80},
        sentiment_data={'limit_down': 20, 'max_continuous': 5},
        index_data={'deviation': -1.0},
        policy_status='中性',
        external_risk='neutral'
    )
    print(f"流动性枯竭触发: {veto}")
    assert veto is not None and '流动性' in veto

    # 模拟：正常行情，不触发否决
    veto_none = check_veto(
        funds_data={'total_amount': 12000, 'north_net_inflow': 30},
        sentiment_data={'limit_down': 5, 'max_continuous': 8},
        index_data={'deviation': 1.5},
        policy_status='中性',
        external_risk='neutral'
    )
    print(f"正常行情否决(应为None): {veto_none}")
    assert veto_none is None
    print("✅ PASS")
except Exception as e:
    print(f"❌ FAIL: {e}")

# ─── Test 6: 完整诊断流程 ─────────────────────────────────────────────────────
separator("Test 6: 完整诊断 run_full_diagnosis()")
try:
    from app.services.layer1_engine import run_full_diagnosis
    diagnosis = run_full_diagnosis(external_risk='neutral')

    # 验证必要字段
    required_keys = ['score', 'mode', 'max_position', 'dimensions', 'directions', 'veto_reason', 'data_date']
    for k in required_keys:
        assert k in diagnosis, f"缺少字段: {k}"
    
    print(f"模式: {diagnosis['mode']}")
    print(f"评分: {diagnosis['score']}")
    print(f"仓位上限: {diagnosis['max_position']}")
    print(f"一票否决: {diagnosis['veto_reason']}")
    print(f"诊断日期: {diagnosis['data_date']}")
    print("六维评分:")
    for k, v in diagnosis['dimensions'].items():
        print(f"  {k}: {v['status']} ({v['score']} 分)")
    print(f"候选板块数量: {len(diagnosis['directions'])}")
    print("✅ PASS")
except Exception as e:
    print(f"❌ FAIL: {e}")
    import traceback; traceback.print_exc()

print("\n" + "="*60)
print("  Phase 1 集成测试完成")
print("="*60 + "\n")

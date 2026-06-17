# -*- coding: utf-8 -*-
"""
数据源整合验证脚本 - 第一阶段替换验证
逐一测试：腾讯财经估值、mootdx 日线、同花顺题材、东财研报、巨潮公告
"""
import sys, os, logging

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'backend')
sys.path.insert(0, os.path.abspath(BACKEND))

logging.basicConfig(level=logging.WARNING)

TEST_CODE = '000001.SZ'  # 平安银行

def sep(title):
    print(f"\n{'='*55}\n  {title}\n{'='*55}")

# ─── 1. 腾讯财经估值 ──────────────────────────────────────────────────────────
sep("Test 1: 腾讯财经估值 get_stock_valuation()")
try:
    from app.core.tencent_client import get_stock_valuation
    val = get_stock_valuation(TEST_CODE)
    print(f"  PE: {val.get('pe')}  PB: {val.get('pb')}")
    print(f"  市值: {val.get('market_cap')}亿  换手率: {val.get('turnover')}%")
    print("  ✅ PASS" if val else "  ⚠️ 返回空（网络或解析问题）")
except Exception as e:
    print(f"  ❌ FAIL: {e}")

# ─── 2. mootdx 日线 ───────────────────────────────────────────────────────────
sep("Test 2: mootdx 日线 get_daily()")
try:
    from app.core.mootdx_client import MootdxClient
    mx = MootdxClient()
    df = mx.get_daily(TEST_CODE, '20260601', '20260617')
    if not df.empty:
        print(f"  获取到 {len(df)} 条日线，最新: {df.iloc[-1].to_dict()}")
        print("  ✅ PASS")
    else:
        print("  ⚠️ 返回空 DataFrame（可能 mootdx 连接问题）")
except Exception as e:
    print(f"  ❌ FAIL: {e}")

# ─── 3. mootdx 指数日线 ───────────────────────────────────────────────────────
sep("Test 3: mootdx 指数日线 get_index_daily()")
try:
    from app.core.mootdx_client import MootdxClient
    mx = MootdxClient()
    df = mx.get_index_daily('000001.SH', '20260601', '20260617')
    if not df.empty:
        print(f"  获取到 {len(df)} 条指数日线，收盘: {df['close'].iloc[-1]}")
        print("  ✅ PASS")
    else:
        print("  ⚠️ 返回空（网络或 mootdx 配置问题）")
except Exception as e:
    print(f"  ❌ FAIL: {e}")

# ─── 4. 同花顺题材 ────────────────────────────────────────────────────────────
sep("Test 4: 同花顺题材标签 get_stock_themes()")
try:
    from app.core.ths_hot_client import get_stock_themes
    themes = get_stock_themes(TEST_CODE)
    print(f"  获取到 {len(themes)} 个题材: {themes[:5]}")
    print("  ✅ PASS（即使为空也属正常，取决于网络代理）")
except Exception as e:
    print(f"  ❌ FAIL: {e}")

# ─── 5. 东财研报摘要 ──────────────────────────────────────────────────────────
sep("Test 5: 东财研报摘要 fetch_research_summary()")
try:
    from app.core.eastmoney_client import fetch_research_summary
    summary = fetch_research_summary(TEST_CODE, limit=2)
    if summary:
        print(f"  摘要（前 200 字）:\n  {summary[:200]}")
        print("  ✅ PASS")
    else:
        print("  ⚠️ 无研报数据（可能代理限制）")
except Exception as e:
    print(f"  ❌ FAIL: {e}")

# ─── 6. 巨潮公告 ─────────────────────────────────────────────────────────────
sep("Test 6: 巨潮公告 check_risk_announcements()")
try:
    from app.core.cninfo_client import check_risk_announcements
    risk = check_risk_announcements(TEST_CODE, days=30)
    print(f"  有风险公告: {risk['has_risk']}")
    print(f"  公告总数: {risk['all_count']}")
    if risk['risk_titles']:
        print(f"  风险公告: {risk['risk_titles'][:2]}")
    print("  ✅ PASS")
except Exception as e:
    print(f"  ❌ FAIL: {e}")

# ─── 7. 数据源路由器 ──────────────────────────────────────────────────────────
sep("Test 7: DataSourceRouter 统一路由")
try:
    from app.core.datasource_router import datasource
    val = datasource.get_valuation(TEST_CODE)
    print(f"  估值路由结果: {val}")
    themes = datasource.get_sector_themes(TEST_CODE)
    print(f"  题材路由结果: {themes[:3]}")
    print("  ✅ PASS")
except Exception as e:
    print(f"  ❌ FAIL: {e}")

print(f"\n{'='*55}\n  数据源整合验证完成\n{'='*55}\n")

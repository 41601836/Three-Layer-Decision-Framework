# -*- coding: utf-8 -*-
"""
test_board_link_siphon.py —— 跨板块联动与二次资金虹吸模块 (board_link_siphon.py) 集成自测试脚本
========================================================================================

本测试验证以下场景，利用 SQLite 数据注入与隔离机制，保障测试的纯净性：
1. 多板块强正向联动、无严重虹吸场景（flow_status = 继续）
2. 精细化强虹吸拦截场景（flow_status = 终止，触发规避策略）
3. 历史数据缺失降级场景（联动默认无，虹吸默认无，不崩溃）
"""

import sys
import sqlite3
import pandas as pd
from db.dao import dao
from decision_framework.board_rank import board_rank
from decision_framework.board_style import board_style
from decision_framework.board_rotation import board_rotation
from decision_framework.board_position import board_position
from decision_framework.board_link_siphon import board_link_siphon
from config_loader import *

# 模拟交易日序列
DATES = ["20260609", "20260610", "20260611", "20260612", "20260613"]
LATEST_DATE = DATES[-1]

original_get_conn = None
mem_conn = None

class MemConnWrapper:
    def __init__(self, conn):
        self.conn = conn
    def __getattr__(self, name):
        return getattr(self.conn, name)
    def close(self):
        pass


def init_db_tables():
    """
    建立测试需要的临时表并填充基础个股定义
    """
    conn = dao.get_conn()
    cursor = conn.cursor()
    try:
        # A. 确保 board_money_flow 表存在
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS board_money_flow (
                board_name TEXT, trade_date TEXT, net_amount REAL, limit_up_count INTEGER,
                leader_height INTEGER, tier_complete INTEGER, year_rise REAL, historical_match INTEGER,
                flow_5d REAL, cover_ratio REAL, tier_status TEXT, sentry_status TEXT, retreat_ratio REAL, week_rise REAL
            )
        """)
        
        # B. 注入测试个股定义
        # 半导体 (科技)
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000001.SZ', '半导体A', '半导体')")
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000002.SZ', '半导体B', '半导体')")
        # 人工智能 (科技)
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000003.SZ', 'AI_A', '人工智能')")
        # 白酒 (消费)
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000004.SZ', '白酒A', '白酒')")
        
        conn.commit()
    except Exception as e:
        print(f"[InitDB] 异常: {e}")
    finally:
        conn.close()


def clean_db_tables():
    """
    清除测试注入的脏数据
    """
    global original_get_conn, mem_conn
    if original_get_conn is not None:
        dao.get_conn = original_get_conn
        original_get_conn = None
    if mem_conn is not None:
        mem_conn = None

    conn = dao.get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("DROP TABLE IF EXISTS board_money_flow")
        cursor.execute("DELETE FROM stock_list WHERE ts_code IN ('000001.SZ', '000002.SZ', '000003.SZ', '000004.SZ')")
        cursor.execute("DELETE FROM daily_prices WHERE ts_code IN ('000001.SZ', '000002.SZ', '000003.SZ', '000004.SZ')")
        conn.commit()
    except Exception as e:
        print(f"[CleanDB] 失败: {e}")
    finally:
        conn.close()


# ==========================================
# 场景 1：多板块正向联动与虹吸平静测试
# ==========================================
def setup_scenario_1():
    conn = dao.get_conn()
    cursor = conn.cursor()
    
    cursor.execute("INSERT INTO board_money_flow VALUES ('半导体', '20260613', 20000000.0, 3, 4, 1, 0.10, 1, 20000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    cursor.execute("INSERT INTO board_money_flow VALUES ('人工智能', '20260613', 15000000.0, 3, 4, 1, 0.10, 1, 15000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    cursor.execute("INSERT INTO board_money_flow VALUES ('白酒', '20260613', 150000000.0, 3, 4, 1, 0.10, 1, 150000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    
    # 注入过去 5 天的价格与资金流历史，使半导体和人工智能同向大涨 (相关系数超 0.9，同步天数 100%)
    price_base_1 = 10.0
    price_base_2 = 20.0
    for i, dt in enumerate(DATES):
        p1 = price_base_1 + i * 1.0  # 10.0, 11.0, 12.0, 13.0, 14.0
        p2 = price_base_2 + i * 2.0  # 20.0, 22.0, 24.0, 26.0, 28.0
        
        # 半导体个股价格
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000001.SZ', ?, 2.0, 1000000000.0, ?, ?, ?)", (dt, p1, p1+0.2, p1-0.2))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000002.SZ', ?, 2.0, 1000000000.0, ?, ?, ?)", (dt, p1, p1+0.2, p1-0.2))
        # 人工智能个股价格
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000003.SZ', ?, 2.0, 1000000000.0, ?, ?, ?)", (dt, p2, p2+0.4, p2-0.4))
        # 资金流动注入
        cursor.execute("INSERT INTO board_money_flow VALUES ('半导体', ?, 50000000.0, 0, 0, 1, 0.05, 1, 50000000.0, 0.1, '完整', '未消耗', 0.1, 0.01)", (dt,))
        cursor.execute("INSERT INTO board_money_flow VALUES ('人工智能', ?, 40000000.0, 0, 0, 1, 0.05, 1, 40000000.0, 0.1, '完整', '未消耗', 0.1, 0.01)", (dt,))

    conn.commit()
    conn.close()


# ==========================================
# 场景 2：触发精细化强虹吸拦截测试
# ==========================================
def setup_scenario_2():
    conn = dao.get_conn()
    cursor = conn.cursor()
    
    cursor.execute("INSERT INTO board_money_flow VALUES ('半导体', '20260613', 500000000.0, 5, 4, 1, 0.10, 1, 500000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    cursor.execute("INSERT INTO board_money_flow VALUES ('白酒', '20260613', -150000000.0, 3, 4, 1, 0.10, 1, -150000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    
    for dt in DATES:
        is_latest = (dt == LATEST_DATE)
        p1 = 12.0
        p2 = 10.0
        # 白酒个股
        # 最新一日白酒跌幅巨大 (流出 1.5亿)
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000004.SZ', ?, -8.0, 400000000.0, ?, ?, ?)", (dt, p2, p2, p2))
        # 半导体个股 (科技强势)
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000001.SZ', ?, 9.95, 3000000000.0, ?, ?, ?)", (dt, p1, p1, p1))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000002.SZ', ?, 9.95, 1500000000.0, ?, ?, ?)", (dt, p1, p1, p1))
        
        # 历史资金流
        cursor.execute("INSERT INTO board_money_flow VALUES ('半导体', ?, 30000000.0, 0, 0, 1, 0.05, 1, 30000000.0, 0.1, '完整', '未消耗', 0.1, 0.01)", (dt,))
        cursor.execute("INSERT INTO board_money_flow VALUES ('白酒', ?, -10000000.0, 0, 0, 1, 0.05, 1, -10000000.0, 0.1, '完整', '未消耗', 0.1, 0.01)", (dt,))

    conn.commit()
    conn.close()


def run_one_test(scenario_name: str, setup_func) -> dict:
    """
    运行单个联动与虹吸自测场景
    """
    print(f"\n=================== 运行测试: {scenario_name} ===================")
    clean_db_tables()
    init_db_tables()
    
    setup_func()
    
    try:
        if scenario_name == "历史数据缺失降级兜底测试":
            style_res = {
                "style_group": [
                    {
                        "style_name": "科技",
                        "board_list": ["半导体"],
                        "intraday_strength": "数据不足",
                        "cross_day_strength": "数据不足"
                    }
                ],
                "data_missing_list": ["风格成分股缺失"]
            }
            rot_res = {
                "rotate_strength": "弱轮动"
            }
            pos_res = {
                "market_total_pos": 0.0,
                "style_position": []
            }
        else:
            # A. 运行上层链条
            board_res = board_rank.run()
            style_res = board_style.run(board_res)
            rot_res = board_rotation.run(style_res)
            
            # 为场景 2 模拟较高的主线风格持仓 (42% 持仓)，以触发联动仓位修正
            if scenario_name == "精细化强虹吸风控硬拦截测试":
                pos_positions = {"半导体": 0.35}
            else:
                pos_positions = {"半导体": 0.05}
                
            pos_res = board_position.run(style_res, rot_res, pos_positions)
        
        # B. 运行子模块5 联动虹吸收官
        siphon_res = board_link_siphon.run(style_res, rot_res, pos_res)
        return siphon_res
    except Exception as e:
        print(f"❌ 运行测试异常: {e}")
        import traceback
        traceback.print_exc()
        return {}


def verify_results(scenario_id: int, result: dict) -> bool:
    if not result:
        print("❌ [ASSERT] 返回结果为空字典！")
        return False
        
    success = True
    if scenario_id == 1:
        # 预期：半导体与人工智能有强正向联动，且无严重虹吸
        links = result["link_info"]
        assert len(links) > 0, "联动对识别为空"
        link_pair = [x for x in links if "半导体 - 人工智能" in x["board_group"]][0]
        assert link_pair["link_level"] == "强联动", f"联动等级不符: {link_pair['link_level']}"
        assert link_pair["link_dir"] == "正向联动", f"联动方向不符: {link_pair['link_dir']}"
        
        siphon = result["siphon_info"]
        assert siphon["siphon_level"] in ["无虹吸", "弱虹吸"], f"虹吸级别错误: {siphon['siphon_level']}"
        assert result["flow_status"] == "继续", f"流程拦截状态错误: {result['flow_status']}"
        
    elif scenario_id == 2:
        # 预期：强虹吸，流程硬终止拦截
        siphon = result["siphon_info"]
        assert siphon["siphon_level"] == "强虹吸", f"虹吸等级错误: {siphon['siphon_level']}"
        assert "半导体" in siphon["influence_range"] and "白酒" in siphon["influence_range"], f"影响范围错误: {siphon['influence_range']}"
        assert result["flow_status"] == "终止", f"流程拦截状态错误: {result['flow_status']}"
        assert "强虹吸风控硬性拦截" in result["risk_warn"] or "【极高风险】" in result["risk_warn"], f"风险提示不符: {result['risk_warn']}"
        assert len(result["strategy"]["avoid_list"]) > 0, "规避清单为空"
        
    elif scenario_id == 3:
        # 预期：价格/流向等数据为空，触发降级
        assert result["flow_status"] == "继续", f"无数据时应允许流程继续，却被终止: {result['flow_status']}"
        assert result["siphon_info"]["siphon_level"] == "无虹吸", f"缺失数据时虹吸应降级，却为: {result['siphon_info']['siphon_level']}"
        assert len(result["data_missing_list"]) > 0, "未捕获缺失清单"
        
    print(f"🎯 [ASSERT] 场景 {scenario_id} 校验成功！")
    return success


def main():
    print("🚀 开始进行跨板块联动与二次资金虹吸自测验证...")
    
    # 场景3 setup
    def setup_scenario_3():
        global original_get_conn, mem_conn
        print("[MockDB] 正在为场景3启用空白内存库隔离 Mock，避免修改/清空真实数据库价格数据...")
        original_get_conn = dao.get_conn
        
        raw_conn = sqlite3.connect(":memory:")
        mem_cursor = raw_conn.cursor()
        mem_cursor.execute("""
            CREATE TABLE daily_prices (
                ts_code TEXT, trade_date TEXT, pct_chg REAL, amount REAL, close REAL, high REAL, low REAL
            )
        """)
        mem_cursor.execute("""
            CREATE TABLE board_money_flow (
                board_name TEXT, trade_date TEXT, net_amount REAL, limit_up_count INTEGER,
                leader_height INTEGER, tier_complete INTEGER, year_rise REAL, historical_match INTEGER,
                flow_5d REAL, cover_ratio REAL, tier_status TEXT, sentry_status TEXT, retreat_ratio REAL, week_rise REAL
            )
        """)
        raw_conn.commit()
        mem_conn = MemConnWrapper(raw_conn)
        dao.get_conn = lambda: mem_conn
        
    test_cases = [
        (1, "多板块正向联动与虹吸平静测试", setup_scenario_1),
        (2, "精细化强虹吸风控硬拦截测试", setup_scenario_2),
        (3, "历史数据缺失降级兜底测试", setup_scenario_3),
    ]
    
    total = len(test_cases)
    passed = 0
    
    try:
        for idx, name, setup_fn in test_cases:
            res = run_one_test(name, setup_fn)
            print(f"运行结果: {res}")
            if verify_results(idx, res):
                passed += 1
            else:
                print(f"❌ 场景 {idx} [{name}] 校验失败！")
    finally:
        clean_db_tables()
        
    print(f"\n=================== 测试统计 ===================")
    print(f"共运行测试项: {total} | 通过: {passed} | 失败: {total - passed}")
    if passed == total:
        print("🎉 恭喜！跨板块联动与二次资金虹吸模块全场景 100% 校验成功通过！")
        sys.exit(0)
    else:
        print("🚨 部分场景测试不通过，请检查代码逻辑！")
        sys.exit(1)


if __name__ == "__main__":
    main()

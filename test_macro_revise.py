# -*- coding: utf-8 -*-
"""
test_macro_revise.py —— 盘中实时修正机制全功能自测试脚本
=====================================================

测试三大规则在不同场景下的触发、计算与模式修正走向。
"""

import sqlite3
from db.dao import dao
from decision_framework.macro_revise import macro_revise
from decision_framework.macro_score import macro_score
from decision_framework.macro_query import macro_query


def setup_mock_db_data():
    """
    往数据库中注入测试成交量数据的 mock 记录，用于规则1（成交额线性外推）的测试
    昨日全天：20260612 成交额 = 100 亿 (10,000,000,000)
    今日早盘：2026-06-13 10:30 快照成交额 = 40 亿 (外推 80 亿，缩量 20% > 15%)
    今日午后：2026-06-13 14:00 快照成交额 = 70 亿 (外推 93.3 亿，缩量 6.7% <= 15%)
    """
    print("\n[MockDB] 开始注入临时测试数据...")
    conn = dao.get_conn()
    cursor = conn.cursor()
    try:
        # 1. 注入昨日价格成交额
        cursor.execute("""
            INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, amount)
            VALUES ('DUMMY.SH', '20260612', 10000000000.0)
        """)
        
        # 2. 注入今日 10:30 缩量快照
        cursor.execute("""
            INSERT OR REPLACE INTO market_snapshot (snapshot_time, trade_date, total_amount)
            VALUES ('2026-06-13 10:30', '2026-06-13', 4000000000.0)
        """)
        
        # 3. 注入今日 14:00 收窄快照
        cursor.execute("""
            INSERT OR REPLACE INTO market_snapshot (snapshot_time, trade_date, total_amount)
            VALUES ('2026-06-13 14:00', '2026-06-13', 7000000000.0)
        """)
        
        conn.commit()
        print("[MockDB] 注入成功。")
    except Exception as e:
        print(f"[MockDB] 注入失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def teardown_mock_db_data():
    """
    清理临时注入的数据
    """
    print("\n[MockDB] 开始清理临时测试数据...")
    conn = dao.get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM daily_prices WHERE ts_code = 'DUMMY.SH' AND trade_date = '20260612'")
        cursor.execute("DELETE FROM market_snapshot WHERE trade_date = '2026-06-13'")
        conn.commit()
        print("[MockDB] 清理成功。")
    except Exception as e:
        print(f"[MockDB] 清理失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def run_test_case(name: str, pre_score: dict, pre_expect: dict, ext_data: dict = None):
    print(f"\n==================== 测试用例: {name} ====================")
    
    res = macro_revise.run(pre_score, pre_expect, ext_data)
    
    print(f"盘前原始模式: {res['original_mode']}")
    print(f"修正后模式: {res['revised_mode']}")
    print(f"修正后仓位: {res['position_limit']:.0%}")
    print(f"流向状态: {res['flow_status']}")
    print("修正明细：")
    for item in res["revise_items"]:
        print(f"  - {item['rule_name']} | {item['status']} | {item['reason']}")
    print(f"板块调整建议: {res['board_adjust']}")
    if res["data_missing_list"]:
        print(f"数据缺失项: {res['data_missing_list']}")


def main():
    print("===== 开始执行第一层盘中实时修正引擎自测 =====")
    
    # 模拟盘前原始结果
    pre_score_attack = {
        "operate_mode": "进攻",
        "position_limit": 0.8,
        "flow_status": "继续",
        "total_score": 4.5,
        "data_missing_list": []
    }
    pre_score_cautious = {
        "operate_mode": "谨慎",
        "position_limit": 0.5,
        "flow_status": "继续",
        "total_score": 3.0,
        "data_missing_list": []
    }
    
    pre_expect = {
        "up_down_ratio": 1.1,
        "top_board_change": 2.5,
        "limit_up_num": 20
    }
    
    # 1. 数据缺失优雅降级测试 (临时清理或清空快照来测试)
    run_test_case("数据缺失优雅降级测试", pre_score_cautious, pre_expect, ext_data=None)
    
    # 2. 注入数据库 mock 记录，运行真实数据库相关的测试
    setup_mock_db_data()
    try:
        # 用例二：测试规则1（10:30早盘缩量超标，禁止降级为谨慎）
        # 昨日 100 亿，今日早盘外推 80 亿，缩量 20% > 15%。
        # 盘前由于某种原因算出来是“谨慎”，但前一日是“进攻”，此时应禁止直接降级为谨慎，修正回“进攻”
        ext_data_rule1_1030 = {
            "prev_operate_mode": "进攻"
        }
        # 注意：因为我们需要让 macro_query.get_snapshot_1030() 能查到我们注入的 2026-06-13 数据，
        # 我们需要在运行测试时临时模拟或指定日期。
        # 我们可以通过临时在 macro_query 的 trade_date 设置来实现。
        # 在此我们直接在 run() 时，由于我们注入的是最新日期（2026-06-13 领先于库中的其他日期），
        # macro_query.get_snapshot_1030() 会自动选出最新的这一天（即 2026-06-13）！
        run_test_case("规则1：早盘缩量超标限制降级 (10:30)", pre_score_cautious, pre_expect, ext_data_rule1_1030)
        
        # 用例三：外盘承接力超预期
        ext_data_rule2 = {
            "foreign_semiconductor_drop": 4.0,
            "a_semiconductor_drop": 0.8
        }
        run_test_case("规则2：外盘芯片暴跌-A股半导体承接力强 (推翻谨慎)", pre_score_cautious, pre_expect, ext_data_rule2)
        
        # 用例四：情绪自适应向上纠偏
        ext_data_rule3_up = {
            "actual_ratio": 1.5,
            "actual_top_change": 3.5,
            "actual_limit_up": 30
        }
        run_test_case("规则3：情绪好于预判 (自适应向上纠偏)", pre_score_cautious, pre_expect, ext_data_rule3_up)

        # 用例五：情绪自适应向下纠偏
        ext_data_rule3_down = {
            "actual_ratio": 0.5,
            "actual_top_change": 1.0,
            "actual_limit_up": 8
        }
        run_test_case("规则3：情绪差于预判 (自适应向下纠偏)", pre_score_attack, pre_expect, ext_data_rule3_down)
        
    finally:
        # 清理 mock 数据
        teardown_mock_db_data()


if __name__ == "__main__":
    main()

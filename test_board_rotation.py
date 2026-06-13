# -*- coding: utf-8 -*-
"""
test_board_rotation.py —— 板块轮动信号识别模块 (board_rotation.py) 全维度集成自测脚本
================================================================================

本脚本针对 board_rotation.py 包含的日内轮动判定、跨日更迭判定、高低位切换判定、
综合强度评估及实操交易信号级联输出等核心业务进行全面的测试。通过模拟数据库中的
成交、涨幅、连板、资金流，验证在以下 9 个核心场景下轮动判定与交易指导的准确性：

1. 基础平稳无异动状态测试
2. 日内“急拉急跌”形态触发测试
3. 日内“冲高回落”形态触发测试
4. 跨日“快速轮动”形态触发测试
5. 跨日“平稳轮动” + “急拉急跌” -> “短线参与”测试
6. 高低切换 —— 主动突破型切换（布局低位）测试
7. 高低切换 —— 被动避险型切换（规避高位）测试
8. 多轮动信号叠加触发强轮动（观望）测试
9. 数据缺失极端降级兜底测试
"""

import sys
import sqlite3
import pandas as pd
from db.dao import dao
from decision_framework.board_style import board_style
from decision_framework.board_rank import board_rank
from decision_framework.board_rotation import board_rotation
from config_loader import *

# 模拟日期序列 (最新日期为 20260613)
DATES = ["20260610", "20260611", "20260612", "20260613"]
LATEST_DATE = DATES[-1]

# 用于场景9的内存库隔离 Mock 全局变量
original_get_conn = None
mem_conn = None



def init_db_tables():
    """
    初始化测试所需的临时表和必要字段
    """
    conn = dao.get_conn()
    cursor = conn.cursor()
    try:
        # A. 临时建第一层板块表并写数据以过前置
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS board_money_flow (
                board_name TEXT, trade_date TEXT, net_amount REAL, limit_up_count INTEGER,
                leader_height INTEGER, tier_complete INTEGER, year_rise REAL, historical_match INTEGER,
                flow_5d REAL, cover_ratio REAL, tier_status TEXT, sentry_status TEXT, retreat_ratio REAL, week_rise REAL
            )
        """)
        conn.commit()
    except Exception as e:
        print(f"[InitDB] 失败: {e}")
    finally:
        conn.close()


def clean_db_tables():
    """
    清理临时表以及测试插入的垃圾数据
    """
    global original_get_conn, mem_conn
    
    # 恢复 Mock 的 get_conn 连接
    if original_get_conn is not None:
        dao.get_conn = original_get_conn
        original_get_conn = None
        
    if mem_conn is not None:
        mem_conn = None
        
    conn = dao.get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("DROP TABLE IF EXISTS board_money_flow")
        cursor.execute("DELETE FROM stock_list WHERE ts_code IN ('000001.SZ', '000002.SZ', '000003.SZ', '000004.SZ', 'DUMMY_STOCK')")
        cursor.execute("DELETE FROM daily_prices WHERE ts_code IN ('000001.SZ', '000002.SZ', '000003.SZ', '000004.SZ', 'DUMMY_STOCK')")
        conn.commit()
    except Exception as e:
        print(f"[CleanDB] 失败: {e}")
    finally:
        conn.close()



def insert_base_stocks():
    """
    注入两个典型行业的股票
    000001.SZ, 000002.SZ 属于“半导体”（科技风格）
    000003.SZ, 000004.SZ 属于“白酒”（消费风格）
    """
    conn = dao.get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000001.SZ', '半导体A', '半导体')")
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000002.SZ', '半导体B', '半导体')")
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000003.SZ', '白酒A', '白酒')")
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000004.SZ', '白酒B', '白酒')")
        conn.commit()
    except Exception as e:
        print(f"[InsertBaseStocks] 异常: {e}")
    finally:
        conn.close()


def run_one_test(scenario_name: str, setup_func) -> dict:
    """
    运行单项场景测试并返回结果
    """
    print(f"\n=================== 运行测试: {scenario_name} ===================")
    clean_db_tables()
    init_db_tables()
    insert_base_stocks()
    
    # 执行具体场景的 mock 数据注入
    setup_func()
    
    try:
        # 1. 运行第一层，筛选出候选板块
        if scenario_name == "数据缺失降级与缺失项搜集测试":
            board_res = {
                "flow_status": "继续",
                "board_list": [{"board_name": "半导体", "total_score": 4.5}]
            }
        else:
            board_res = board_rank.run()
        # 2. 运行第二层风格划分
        style_res = board_style.run(board_res)
        # 3. 运行板块轮动模块
        rot_res = board_rotation.run(style_res)
        return rot_res
    except Exception as e:
        print(f"❌ 运行测试异常: {e}")
        import traceback
        traceback.print_exc()
        return {}


# ==========================================
# 场景 1：基础平稳无异动状态测试
# ==========================================
def setup_scenario_1():
    conn = dao.get_conn()
    cursor = conn.cursor()
    # 注入候选板块
    cursor.execute("INSERT INTO board_money_flow VALUES ('半导体', '20260613', 100000000.0, 1, 1, 1, 0.10, 1, 100000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    cursor.execute("INSERT INTO board_money_flow VALUES ('白酒', '20260613', 100000000.0, 1, 1, 1, 0.10, 1, 100000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    
    # 注入 4 天的价格。为了让跨日轮动判定为“无明显轮动”，4天中最强的都是科技风格
    for dt in DATES:
        # 科技风格（半导体，大涨，大成交）
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000001.SZ', ?, 2.0, 3000000000.0, 10.0, 10.2, 9.8)", (dt,))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000002.SZ', ?, 2.0, 1000000000.0, 15.0, 15.3, 14.7)", (dt,))
        # 消费风格（白酒，温和）
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000003.SZ', ?, 0.5, 500000000.0, 20.0, 20.1, 19.9)", (dt,))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000004.SZ', ?, 0.5, 500000000.0, 25.0, 25.1, 24.9)", (dt,))
        # 市场参照股 (使科技占比高)
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('DUMMY_STOCK', ?, 0.0, 2000000000.0, 5.0, 5.0, 5.0)", (dt,))
    
    conn.commit()
    conn.close()


# ==========================================
# 场景 2：日内“急拉急跌”形态触发测试 (振幅 > 8%)
# ==========================================
def setup_scenario_2():
    conn = dao.get_conn()
    cursor = conn.cursor()
    # 注入候选板块
    cursor.execute("INSERT INTO board_money_flow VALUES ('半导体', '20260613', 100000000.0, 1, 1, 1, 0.10, 1, 100000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    
    # 注入价格，使前 3 天科技最强，最新一日科技大振幅 (10%)
    for dt in DATES:
        is_latest = (dt == LATEST_DATE)
        # 最新一日：振幅 = (11.0 - 10.0)/10.0 = 10% > 8%，收盘 10.5
        high_1 = 11.0 if is_latest else 10.2
        low_1 = 10.0 if is_latest else 9.8
        close_1 = 10.5 if is_latest else 10.0
        pct_1 = 5.0 if is_latest else 2.0
        
        high_2 = 16.5 if is_latest else 15.3
        low_2 = 15.0 if is_latest else 14.7
        close_2 = 15.75 if is_latest else 15.0
        pct_2 = 5.0 if is_latest else 2.0
        
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000001.SZ', ?, ?, 3000000000.0, ?, ?, ?)", (dt, pct_1, close_1, high_1, low_1))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000002.SZ', ?, ?, 1000000000.0, ?, ?, ?)", (dt, pct_2, close_2, high_2, low_2))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('DUMMY_STOCK', ?, 0.0, 2000000000.0, 5.0, 5.0, 5.0)", (dt,))
        
    conn.commit()
    conn.close()


# ==========================================
# 场景 3：日内“冲高回落”形态触发测试 (回落 > 5%)
# ==========================================
def setup_scenario_3():
    conn = dao.get_conn()
    cursor = conn.cursor()
    # 注入候选板块
    cursor.execute("INSERT INTO board_money_flow VALUES ('半导体', '20260613', 100000000.0, 1, 1, 1, 0.10, 1, 100000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    
    # 注入价格，使最新一日冲高回落明显：
    # 000001.SZ: high = 11.2, low = 10.0, close = 10.0, pre_close = 10.0 (回落幅度 (11.2-10.0)/11.2 = 10.7% > 5%)
    # 000002.SZ: high = 16.8, low = 15.0, close = 15.0, pre_close = 15.0 (回落幅度 (16.8-15.0)/16.8 = 10.7% > 5%)
    # 振幅均值为 (1.2/10 + 1.8/15)/2 = 12%，但代码中先判定振幅。
    # 为了只触发冲高回落，我们需要将振幅拉低至 8% 以下。
    # 000001.SZ: high = 10.6, low = 10.0, close = 10.0, pre_close = 10.0
    #            振幅 (10.6-10.0)/10.0 = 6% < 8%
    #            回落 (10.6-10.0)/10.6 = 5.66% > 5%
    for dt in DATES:
        is_latest = (dt == LATEST_DATE)
        high_1 = 10.6 if is_latest else 10.2
        low_1 = 10.0 if is_latest else 9.8
        close_1 = 10.0 if is_latest else 10.0
        pct_1 = 0.0 if is_latest else 2.0
        
        high_2 = 15.9 if is_latest else 15.3
        low_2 = 15.0 if is_latest else 14.7
        close_2 = 15.0 if is_latest else 15.0
        pct_2 = 0.0 if is_latest else 2.0
        
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000001.SZ', ?, ?, 3000000000.0, ?, ?, ?)", (dt, pct_1, close_1, high_1, low_1))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000002.SZ', ?, ?, 1000000000.0, ?, ?, ?)", (dt, pct_2, close_2, high_2, low_2))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('DUMMY_STOCK', ?, 0.0, 2000000000.0, 5.0, 5.0, 5.0)", (dt,))
        
    conn.commit()
    conn.close()


# ==========================================
# 场景 4：跨日“快速轮动”形态触发测试
# ==========================================
def setup_scenario_4():
    conn = dao.get_conn()
    cursor = conn.cursor()
    # 注入候选板块，保证科技、消费都有候选以评估
    cursor.execute("INSERT INTO board_money_flow VALUES ('半导体', '20260613', 100000000.0, 1, 1, 1, 0.10, 1, 100000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    cursor.execute("INSERT INTO board_money_flow VALUES ('白酒', '20260613', 100000000.0, 1, 1, 1, 0.10, 1, 100000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    
    # 切换交替，最强风格交替: 20260610 (科技强), 20260611 (消费强), 20260612 (科技强), 20260613 (消费强)
    # 科技的成交在 T-3 / T-1 时极大，消费在 T-2 / T 时极大。且最强那天狂拉涨停。
    for i, dt in enumerate(DATES):
        is_tech_day = (i % 2 == 0)
        
        # 科技价格注入
        t_amt = 5000000000.0 if is_tech_day else 500000000.0
        t_pct = 9.95 if is_tech_day else 0.5
        t_high = 11.0 if is_tech_day else 10.1
        t_close = 11.0 if is_tech_day else 10.0
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000001.SZ', ?, ?, ?, ?, ?, 9.8)", (dt, t_pct, t_amt, t_close, t_high))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000002.SZ', ?, ?, ?, ?, ?, 14.8)", (dt, t_pct, t_amt/2, t_close*1.5, t_high*1.5))
        
        # 消费价格注入
        c_amt = 5000000000.0 if not is_tech_day else 500000000.0
        c_pct = 9.95 if not is_tech_day else 0.5
        c_high = 22.0 if not is_tech_day else 20.1
        c_close = 22.0 if not is_tech_day else 20.0
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000003.SZ', ?, ?, ?, ?, ?, 19.8)", (dt, c_pct, c_amt, c_close, c_high))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000004.SZ', ?, ?, ?, ?, ?, 24.8)", (dt, c_pct, c_amt/2, c_close*1.2, c_high*1.2))
        
        # 参照股
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('DUMMY_STOCK', ?, 0.0, 2000000000.0, 5.0, 5.0, 5.0)", (dt,))
        
    conn.commit()
    conn.close()


# ==========================================
# 场景 5：跨日“平稳轮动” + “急拉急跌” -> “短线参与”测试
# ==========================================
def setup_scenario_5():
    conn = dao.get_conn()
    cursor = conn.cursor()
    # 注入候选板块
    cursor.execute("INSERT INTO board_money_flow VALUES ('半导体', '20260613', 100000000.0, 1, 1, 1, 0.10, 1, 100000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    cursor.execute("INSERT INTO board_money_flow VALUES ('白酒', '20260613', 100000000.0, 1, 1, 1, 0.10, 1, 100000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    
    # 保证最强风格序列为: 20260610 (科技), 20260611 (科技), 20260612 (消费), 20260613 (消费)
    # 相邻不一致频次 = 1 (平稳轮动)。
    # 同时最新一日触发“急拉急跌”(振幅 > 8%)。
    for i, dt in enumerate(DATES):
        is_tech_day = (i <= 1)
        is_latest = (dt == LATEST_DATE)
        
        # 科技价格注入
        t_amt = 5000000000.0 if is_tech_day else 500000000.0
        t_pct = 9.95 if is_tech_day else 0.5
        t_high = 11.0 if is_tech_day else 10.1
        t_close = 11.0 if is_tech_day else 10.0
        # 如果是最新一日，给点振幅，但它不是主线
        if is_latest:
            t_high = 11.0
            t_close = 10.0
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000001.SZ', ?, ?, ?, ?, ?, 9.8)", (dt, t_pct, t_amt, t_close, t_high))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000002.SZ', ?, ?, ?, ?, ?, 14.8)", (dt, t_pct, t_amt/2, t_close*1.5, t_high*1.5))
        
        # 消费价格注入
        c_amt = 5000000000.0 if not is_tech_day else 500000000.0
        c_pct = 9.95 if not is_tech_day else 0.5
        # 最新一日：振幅较大，高 = 22.0，低 = 19.8， pre_close = 20.0， 振幅 = (22-19.8)/20.0 = 11% > 8%，触发急拉急跌
        c_high = 22.2 if is_latest else (22.0 if not is_tech_day else 20.1)
        c_low = 19.8 if is_latest else 19.8
        c_close = 21.0 if is_latest else (22.0 if not is_tech_day else 20.0)
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000003.SZ', ?, ?, ?, ?, ?, ?)", (dt, c_pct, c_amt, c_close, c_high, c_low))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000004.SZ', ?, ?, ?, ?, ?, 24.8)", (dt, c_pct, c_amt/2, c_close*1.2, c_high*1.2))
        
        # 参照股
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('DUMMY_STOCK', ?, 0.0, 2000000000.0, 5.0, 5.0, 5.0)", (dt,))
        
    conn.commit()
    conn.close()


# ==========================================
# 场景 6：高低切换 —— 主动突破型切换（布局低位）
# ==========================================
def setup_scenario_6():
    conn = dao.get_conn()
    cursor = conn.cursor()
    
    # 注入候选板块
    # 半导体（科技风格）今日资金流出，白酒（消费风格）今日资金流入
    cursor.execute("INSERT INTO board_money_flow VALUES ('半导体', '20260613', -10000000.0, 0, 0, 1, 0.10, 1, -10000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    cursor.execute("INSERT INTO board_money_flow VALUES ('白酒', '20260613', 200000000.0, 2, 1, 1, 0.10, 1, 200000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    
    # 1. 科技风格（半导体）：累计 3 日大涨，近 3 日从 10.0 元涨到 12.5 元 (累计涨 25% > 20% 高位)
    # T-3 (20260610)：10.0
    # T (20260613)：12.5
    # 最新一日资金为负流出。
    # 2. 消费风格（白酒）：累计 3 日滞涨，近 3 日从 10.0 元涨到 10.2 元 (累计涨 2% < 5% 低位)
    # T-3 (20260610)：10.0
    # T (20260613)：10.2
    # 最新一日白酒（消费）在 daily_prices 里面有两只涨停股（主动切换）。
    for dt in DATES:
        is_t3 = (dt == "20260610")
        is_t = (dt == LATEST_DATE)
        
        t_close = 12.5 if is_t else (10.0 if is_t3 else 11.0)
        t_pct = 5.0 if is_t else 2.0
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000001.SZ', ?, ?, 10000000.0, ?, ? + 0.1, ? - 0.1)", (dt, t_pct, t_close, t_close, t_close))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000002.SZ', ?, ?, 10000000.0, ?, ? + 0.1, ? - 0.1)", (dt, t_pct, t_close, t_close, t_close))
        
        c_close = 10.2 if is_t else (10.0 if is_t3 else 10.1)
        c_pct = 9.95 if is_t else 0.5  # 最新一日大涨停，触发主动切换判定条件：首板 >= 2
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000003.SZ', ?, ?, 500000000.0, ?, ? + 0.1, ? - 0.1)", (dt, c_pct, c_close, c_close, c_close))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000004.SZ', ?, ?, 500000000.0, ?, ? + 0.1, ? - 0.1)", (dt, c_pct, c_close, c_close, c_close))
        
        # 参照股
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('DUMMY_STOCK', ?, 0.0, 2000000000.0, 5.0, 5.0, 5.0)", (dt,))
        
    conn.commit()
    conn.close()


# ==========================================
# 场景 7：高低切换 —— 被动避险型切换（规避高位）
# ==========================================
def setup_scenario_7():
    conn = dao.get_conn()
    cursor = conn.cursor()
    
    # 注入候选板块
    cursor.execute("INSERT INTO board_money_flow VALUES ('半导体', '20260613', -10000000.0, 0, 0, 1, 0.10, 1, -10000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    cursor.execute("INSERT INTO board_money_flow VALUES ('白酒', '20260613', 200000000.0, 0, 0, 1, 0.10, 1, 200000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    
    # 高位大涨 25%，低位仅涨 2%，但是低位今天涨幅温和（无个股首板涨停，即 pct_chg 均小于 9.5，触发被动避险）
    for dt in DATES:
        is_t3 = (dt == "20260610")
        is_t = (dt == LATEST_DATE)
        
        t_close = 12.5 if is_t else (10.0 if is_t3 else 11.0)
        t_pct = 5.0 if is_t else 2.0
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000001.SZ', ?, ?, 10000000.0, ?, ? + 0.1, ? - 0.1)", (dt, t_pct, t_close, t_close, t_close))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000002.SZ', ?, ?, 10000000.0, ?, ? + 0.1, ? - 0.1)", (dt, t_pct, t_close, t_close, t_close))
        
        c_close = 10.2 if is_t else (10.0 if is_t3 else 10.1)
        c_pct = 1.0 if is_t else 0.5  # 温和上涨，无涨停
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000003.SZ', ?, ?, 500000000.0, ?, ? + 0.1, ? - 0.1)", (dt, c_pct, c_close, c_close, c_close))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000004.SZ', ?, ?, 500000000.0, ?, ? + 0.1, ? - 0.1)", (dt, c_pct, c_close, c_close, c_close))
        
        # 参照股
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('DUMMY_STOCK', ?, 0.0, 2000000000.0, 5.0, 5.0, 5.0)", (dt,))
        
    conn.commit()
    conn.close()


# ==========================================
# 场景 8：多信号叠加触发强轮动（观望）
# ==========================================
def setup_scenario_8():
    conn = dao.get_conn()
    cursor = conn.cursor()
    # 同时触发：日内急拉急跌 + 资金高低切换
    # 设为符合初筛有效性的值，使半导体和白酒都成为观察板块
    cursor.execute("INSERT INTO board_money_flow VALUES ('半导体', '20260613', -10000000.0, 3, 4, 1, 0.10, 1, -10000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    cursor.execute("INSERT INTO board_money_flow VALUES ('白酒', '20260613', 200000000.0, 3, 4, 1, 0.10, 1, 200000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    
    for dt in DATES:
        is_t3 = (dt == "20260610")
        is_t = (dt == LATEST_DATE)
        
        # 最新一日科技大振幅 10%
        high_1 = 13.75 if is_t else (10.2 if not is_t3 else 10.0)
        low_1 = 12.5 if is_t else (9.8 if not is_t3 else 10.0)
        t_close = 13.5 if is_t else (10.0 if is_t3 else 11.0)
        t_pct = 5.0 if is_t else 2.0
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000001.SZ', ?, ?, 10000000.0, ?, ?, ?)", (dt, t_pct, t_close, high_1, low_1))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000002.SZ', ?, ?, 10000000.0, ?, ?, ?)", (dt, t_pct, t_close, high_1, low_1))
        
        c_close = 10.2 if is_t else (10.0 if is_t3 else 10.1)
        c_pct = 9.95 if is_t else 0.5  # 首板触发
        c_high = 11.2 if is_t else (c_close + 0.1)
        c_low = 10.0 if is_t else (c_close - 0.1)
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000003.SZ', ?, ?, 500000000.0, ?, ?, ?)", (dt, c_pct, c_close, c_high, c_low))
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('000004.SZ', ?, ?, 500000000.0, ?, ?, ?)", (dt, c_pct, c_close, c_high, c_low))
        
        # 参照股
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close, high, low) VALUES ('DUMMY_STOCK', ?, 0.0, 2000000000.0, 5.0, 5.0, 5.0)", (dt,))
        
    conn.commit()
    conn.close()


class MemConnWrapper:
    def __init__(self, conn):
        self.conn = conn
    def __getattr__(self, name):
        return getattr(self.conn, name)
    def close(self):
        # 拦截 close 调用，什么都不做，留待测试框架结束后自动销毁
        pass


# ==========================================
# 场景 9：数据缺失极端降级兜底测试
# ==========================================
def setup_scenario_9():
    global original_get_conn, mem_conn
    print("[MockDB] 正在为场景9启用空白内存库隔离 Mock，避免修改/清空真实数据库价格数据...")
    
    # 1. 备份真实的 get_conn
    original_get_conn = dao.get_conn
    
    # 2. 创建全新的隔离内存库，创建空 daily_prices 以便 MAX(trade_date) 返回 None
    raw_conn = sqlite3.connect(":memory:")
    mem_cursor = raw_conn.cursor()
    mem_cursor.execute("""
        CREATE TABLE daily_prices (
            ts_code TEXT, trade_date TEXT, pct_chg REAL, amount REAL, close REAL, high REAL, low REAL
        )
    """)
    
    # 3. 建立 board_money_flow 并注入符合初筛有效性的半导体板块，使得 active_styles 有科技候选
    mem_cursor.execute("""
        CREATE TABLE board_money_flow (
            board_name TEXT, trade_date TEXT, net_amount REAL, limit_up_count INTEGER,
            leader_height INTEGER, tier_complete INTEGER, year_rise REAL, historical_match INTEGER,
            flow_5d REAL, cover_ratio REAL, tier_status TEXT, sentry_status TEXT, retreat_ratio REAL, week_rise REAL
        )
    """)
    mem_cursor.execute("INSERT INTO board_money_flow VALUES ('半导体', '20260613', 100000000.0, 3, 4, 1, 0.10, 1, 100000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)")
    raw_conn.commit()
    
    # 4. 用 Wrapper 拦截 close，并拦截 get_conn() 返回 Mock 连接
    mem_conn = MemConnWrapper(raw_conn)
    dao.get_conn = lambda: mem_conn



# ==========================================
# 结果校验断言函数
# ==========================================
def verify_results(scenario_id: int, result: dict):
    if not result:
        print("❌ [ASSERT] 运行返回空字典！")
        return False
        
    success = True
    
    if scenario_id == 1:
        # 正常平稳
        assert result["intraday_info"]["type"] == "无异动", f"场景1 日内错误: {result['intraday_info']['type']}"
        assert result["cross_info"]["type"] == "无明显轮动", f"场景1 跨日错误: {result['cross_info']['type']}"
        assert result["hl_switch"]["status"] == "无切换", f"场景1 高低切换错误: {result['hl_switch']['status']}"
        assert result["rotate_strength"] == "弱轮动", f"场景1 强度错误: {result['rotate_strength']}"
        assert result["trade_signal"] == "观望", f"场景1 信号错误: {result['trade_signal']}"
        
    elif scenario_id == 2:
        # 日内急拉急跌
        assert result["intraday_info"]["type"] == "急拉急跌", f"场景2 日内错误: {result['intraday_info']['type']}"
        assert result["rotate_strength"] == "中等轮动", f"场景2 强度错误: {result['rotate_strength']}"
        assert result["trade_signal"] == "观望", f"场景2 信号错误: {result['trade_signal']}"
        
    elif scenario_id == 3:
        # 日内冲高回落
        assert result["intraday_info"]["type"] == "冲高回落", f"场景3 日内错误: {result['intraday_info']['type']}"
        assert result["rotate_strength"] == "中等轮动", f"场景3 强度错误: {result['rotate_strength']}"
        
    elif scenario_id == 4:
        # 跨日快速轮动
        assert result["cross_info"]["type"] == "快速轮动", f"场景4 跨日错误: {result['cross_info']['type']}"
        assert result["rotate_strength"] == "中等轮动", f"场景4 强度错误: {result['rotate_strength']}"
        assert result["trade_signal"] == "观望", f"场景4 信号错误: {result['trade_signal']}"
        
    elif scenario_id == 5:
        # 平稳轮动+日内急拉急跌 -> 短线参与
        assert result["cross_info"]["type"] == "平稳轮动", f"场景5 跨日错误: {result['cross_info']['type']}"
        assert result["intraday_info"]["type"] == "急拉急跌", f"场景5 日内错误: {result['intraday_info']['type']}"
        assert result["rotate_strength"] == "中等轮动", f"场景5 强度错误: {result['rotate_strength']}"
        assert result["trade_signal"] == "短线参与", f"场景5 交易信号错误: {result['trade_signal']}"
        
    elif scenario_id == 6:
        # 高低切换 - 主动突破
        assert result["hl_switch"]["status"] == "有切换", f"场景6 高低位状态错误: {result['hl_switch']['status']}"
        assert result["hl_switch"]["switch_type"] == "主动切换", f"场景6 切换类型错误: {result['hl_switch']['switch_type']}"
        assert result["trade_signal"] == "布局低位", f"场景6 交易信号错误: {result['trade_signal']}"
        
    elif scenario_id == 7:
        # 高低切换 - 被动避险
        assert result["hl_switch"]["status"] == "有切换", f"场景7 高低位状态错误: {result['hl_switch']['status']}"
        assert result["hl_switch"]["switch_type"] == "被动避险", f"场景7 切换类型错误: {result['hl_switch']['switch_type']}"
        assert result["trade_signal"] == "规避高位", f"场景7 交易信号错误: {result['trade_signal']}"
        
    elif scenario_id == 8:
        # 强轮动
        assert result["rotate_strength"] == "强轮动", f"场景8 强度错误: {result['rotate_strength']}"
        assert result["trade_signal"] == "观望", f"场景8 交易信号错误: {result['trade_signal']}"
        
    elif scenario_id == 9:
        # 数据缺失
        assert "数据不足" in result["intraday_info"]["type"] or "缺失" in result["intraday_info"]["desc"] or result["intraday_info"]["type"] == "数据不足", f"场景9 降级错误: {result['intraday_info']}"
        assert result["rotate_strength"] == "弱轮动", f"场景9 降级强度错误: {result['rotate_strength']}"
        assert result["trade_signal"] == "观望", f"场景9 降级信号错误: {result['trade_signal']}"
        assert len(result["data_missing_list"]) > 0, f"场景9 未捕获缺失清单"
        
    print(f"🎯 [ASSERT] 场景 {scenario_id} 校验通过！")
    return success


# ==========================================
# 主运行入口
# ==========================================
def main():
    print("🚀 开始进行板块轮动决策系统集成回归测试...")
    test_cases = [
        (1, "基础平稳无异动状态测试", setup_scenario_1),
        (2, "日内急拉急跌形态触发测试", setup_scenario_2),
        (3, "日内冲高回落形态触发测试", setup_scenario_3),
        (4, "跨日快速轮动形态触发测试", setup_scenario_4),
        (5, "跨日平稳轮动+日内异动->短线参与测试", setup_scenario_5),
        (6, "高低位切换-主动突破型切换测试", setup_scenario_6),
        (7, "高低位切换-被动避险型切换测试", setup_scenario_7),
        (8, "多信号叠加触发强轮动测试", setup_scenario_8),
        (9, "数据缺失降级与缺失项搜集测试", setup_scenario_9),
    ]
    
    total = len(test_cases)
    passed = 0
    
    try:
        for idx, name, setup_fn in test_cases:
            res = run_one_test(name, setup_fn)
            print(f"返回结果: {res}")
            if verify_results(idx, res):
                passed += 1
            else:
                print(f"❌ 场景 {idx} [{name}] 校验失败！")
    finally:
        clean_db_tables()
        
    print(f"\n=================== 测试统计 ===================")
    print(f"共运行测试项: {total} | 通过: {passed} | 失败: {total - passed}")
    if passed == total:
        print("🎉 恭喜！板块轮动判定全场景 100% 校验成功通过！")
        sys.exit(0)
    else:
        print("🚨 部分场景测试不通过，请检查代码逻辑！")
        sys.exit(1)


if __name__ == "__main__":
    main()

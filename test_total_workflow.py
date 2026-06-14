# -*- coding: utf-8 -*-
"""
test_total_workflow.py —— 三层量化决策框架全流程总调度一键测试脚本
============================================================

本脚本独立测试顶层总入口 workflow_total，验证以下四大核心测试场景：
1. 正常行情场景 (验证全链路成功走完、标准化交易指令输出与多因子仓位分配)
2. 否决硬拦截场景 (验证流动性枯竭触发一票否决，第一层宏观诊断提前终止退出)
3. 部分数据缺失降级场景 (验证当历史日线天数不足时，数据缺失去重展现且风控降级生效)
4. 全局异常容错场景 (验证当某一子模块发生 Runtime 严重崩溃时，顶层全局捕获不崩溃并优雅输出)
"""

import sqlite3
import json
import os
from db.dao import dao
from decision_framework.workflow_total import total_workflow
from decision_framework.macro_score import macro_score

# 模拟的日期序列
DATES = [
    "20260525", "20260526", "20260527", "20260528", "20260529",
    "20260601", "20260602", "20260603", "20260604", "20260605",
    "20260608", "20260609", "20260610", "20260611", "20260612",
    "20260613", "20260614", "20260615", "20260616", "20260617"
]
LATEST_DATE = DATES[-1]

original_get_conn = None
mem_conn = None

class MemConnWrapper:
    def __init__(self, conn):
        self.__dict__['conn'] = conn
    def __getattr__(self, name):
        return getattr(self.conn, name)
    def __setattr__(self, name, value):
        setattr(self.conn, name, value)
    def close(self):
        pass

def setup_base_mock_db(is_missing_prices=False, is_liquidity_veto=False):
    """
    配置隔离的内存数据库并注入全流程所需的数据
    """
    global original_get_conn
    original_get_conn = dao.get_conn
    
    raw_conn = sqlite3.connect(":memory:")
    dao.get_conn = lambda: MemConnWrapper(raw_conn)
    
    cursor = raw_conn.cursor()
    # 1. 创建全量表结构
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_list (
            ts_code TEXT PRIMARY KEY, symbol TEXT, name TEXT, area TEXT, industry TEXT, market TEXT, list_date TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_prices (
            ts_code TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL, pre_close REAL, change REAL, pct_chg REAL, vol REAL, amount REAL, adj_factor REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_basic (
            ts_code TEXT, trade_date TEXT, pe REAL, turnover_rate REAL, volume_ratio REAL, free_share REAL, circ_mv REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS board_money_flow (
            board_name TEXT, trade_date TEXT, net_amount REAL, limit_up_count INTEGER,
            leader_height INTEGER, tier_complete INTEGER, year_rise REAL, historical_match INTEGER,
            flow_5d REAL, cover_ratio REAL, tier_status TEXT, sentry_status TEXT, retreat_ratio REAL, week_rise REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS moneyflow (
            ts_code TEXT, trade_date TEXT, buy_sm_vol REAL, buy_sm_amount REAL, sell_sm_vol REAL, sell_sm_amount REAL,
            buy_md_vol REAL, buy_md_amount REAL, sell_md_vol REAL, sell_md_amount REAL,
            buy_lg_vol REAL, buy_lg_amount REAL, sell_lg_vol REAL, sell_lg_amount REAL,
            buy_elg_vol REAL, buy_elg_amount REAL, sell_elg_vol REAL, sell_elg_amount REAL,
            net_mf_vol REAL, net_mf_amount REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stk_holdernumber (
            ts_code TEXT, ann_date TEXT, end_date TEXT, holder_num INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bak_basic (
            ts_code TEXT, trade_date TEXT, profit_yoy REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS global_macro_daily (
            trade_date TEXT PRIMARY KEY, vix REAL, brent_price REAL, brent_pct REAL, dxy REAL, usdcnh REAL, dji_pct REAL, ixic_pct REAL, spx_pct REAL, kospi_pct REAL, n225_pct REAL, ext1 TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS china_macro_indicators (
            trade_date TEXT PRIMARY KEY, pmi_man REAL, cpi REAL, gdp_growth REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hsgt_moneyflow (
            trade_date TEXT PRIMARY KEY, north_money REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_snapshot (
            trade_date TEXT, snapshot_time TEXT, total_amount REAL, half_up_num INTEGER, half_down_num INTEGER, board_top_change REAL, ext1 TEXT, ext2 TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_market_post (
            trade_date TEXT PRIMARY KEY, limit_up INTEGER, limit_down INTEGER, max_board INTEGER, continue_rate REAL
        )
    """)

    # 2. 注入大盘宏观层所需数据
    # 全球宏观：VIX=15.0(安全)，标普=+0.5%，美指=101.2，道指=+0.3%，纳斯达克=+0.6%，原油跌幅，等等
    # 字段顺序：trade_date, vix, brent_price, brent_pct, dxy, usdcnh, dji_pct, ixic_pct, spx_pct, kospi_pct, n225_pct, ext1
    cursor.execute("""
        INSERT INTO global_macro_daily 
        VALUES (?, 15.0, 80.0, -0.5, 101.2, 7.25, 0.3, 0.6, 0.5, 0.1, 0.2, '4.0')
    """, (LATEST_DATE,))
    # 国内宏观：PMI=50.2(扩张)
    cursor.execute("INSERT INTO china_macro_indicators VALUES (?, 50.2, 1.2, 5.0)", (LATEST_DATE,))
    # 北向资金：流入 +1500万 (若是流动性一票否决测试，改大额流出 -60亿)
    north_val = -6000000000.0 if is_liquidity_veto else 15000000.0
    cursor.execute("INSERT INTO hsgt_moneyflow VALUES (?, ?)", (LATEST_DATE, north_val))
    # 市场快照：成交 10:30 快照 4500 亿，上涨比率 1.5，连板 3
    cursor.execute("INSERT INTO market_snapshot VALUES (?, '10:30:00', 450000000000.0, 2400, 1600, 2.5, '1.5', '12')", (LATEST_DATE,))
    # 盘后大盘表现
    cursor.execute("INSERT INTO daily_market_post VALUES (?, 40, 5, 3, 0.65)", (LATEST_DATE,))

    # 3. 注入板块：半导体，属于“科技”风格，设置为强势板块 (流入12亿)
    cursor.execute("""
        INSERT INTO board_money_flow (board_name, trade_date, net_amount, limit_up_count, leader_height, tier_complete, year_rise, historical_match, flow_5d, cover_ratio, tier_status, sentry_status, retreat_ratio, week_rise)
        VALUES ('半导体', ?, 1200000000.0, 5, 4, 1, 0.10, 1, 1200000000.0, 0.12, '完整', '未消耗', 0.10, 0.01)
    """, (LATEST_DATE,))

    # 4. 注入 3 只个股定义
    stocks_meta = [
        ("000001.SZ", "半导体A", "半导体"),  # 优选入场区 + 优质标的
        ("000002.SZ", "半导体B", "半导体"),  # 谨慎入场区 + 良好标的
        ("000003.SZ", "半导体C", "半导体")   # 禁入区 + 一般标的
    ]
    for ts_code, name, industry in stocks_meta:
        cursor.execute("INSERT INTO stock_list (ts_code, name, industry, list_date) VALUES (?, ?, ?, '20200101')", (ts_code, name, industry))

    # 5. 注入历史价格和基本面
    # 如果 is_missing_prices 是 True，我们只为 000001.SZ 注入 5 天历史数据来模拟缺失；
    # 否则，注入 20 天历史数据使之能顺利通过个股初筛
    inject_dates = DATES[-5:] if is_missing_prices else DATES
    
    # 000001.SZ (A股) 均价 10.0，最新收盘 10.20，放量 1500 (放量 >= 1.2)，换手率 5%
    # 000002.SZ (B股) 均价 10.5，最新收盘 12.50，放量 1500，换手率 6%
    # 000003.SZ (C股) 均价 12.0，最新收盘 14.80，放量 1500，换手率 7%
    for dt in inject_dates:
        is_latest = (dt == LATEST_DATE)
        for ts_code in ["000001.SZ", "000002.SZ", "000003.SZ"]:
            # 基准历史价格
            if ts_code == "000001.SZ":
                base_close = 10.0
                hist_high = 12.0
                hist_low = 10.5
            elif ts_code == "000002.SZ":
                base_close = 10.5
                hist_high = 12.0
                hist_low = 10.5
            else: # 000003.SZ
                base_close = 12.0
                hist_high = 13.0
                hist_low = 12.0
                
            # 第1天高点注入 15.0, 第6天低点注入 10.0，锁定 20日支撑/压力位
            if not is_missing_prices:
                if dt == DATES[0]:
                    hist_high = 15.0
                if dt == DATES[5]:
                    hist_low = 10.0
                
            if is_latest:
                close_val = 10.2 if ts_code == "000001.SZ" else (12.5 if ts_code == "000002.SZ" else 14.8)
                high_val = 15.0 if ts_code == "000003.SZ" else (13.0 if ts_code == "000002.SZ" else 12.0)
                low_val = 10.1 if ts_code == "000001.SZ" else (12.0 if ts_code == "000002.SZ" else 14.5)
                vol_val = 1500.0  # 放量
                turnover_val = 5.0
            else:
                close_val = base_close
                high_val = hist_high
                low_val = hist_low
                vol_val = 1000.0  # 均量 1000
                turnover_val = 3.0
                
            # 每日大盘两市成交额
            market_total_amount = 300000000.0 if is_liquidity_veto and dt == LATEST_DATE else 1000000000.0
            
            cursor.execute("""
                INSERT INTO daily_prices (ts_code, trade_date, open, high, low, close, vol, amount, pct_chg) 
                VALUES (?, ?, 11.0, ?, ?, ?, ?, ?, 1.5)
            """, (ts_code, dt, high_val, low_val, close_val, vol_val, market_total_amount))
            
            cursor.execute("""
                INSERT INTO daily_basic (ts_code, trade_date, pe, turnover_rate, circ_mv) 
                VALUES (?, ?, 15.0, ?, 1500000.0)
            """, (ts_code, dt, turnover_val))
            
    # 6. 注入个股打分面指标数据
    for ts_code in ["000001.SZ", "000002.SZ", "000003.SZ"]:
        growth_val = 45.0 if ts_code == "000001.SZ" else (20.0 if ts_code == "000002.SZ" else 5.0)
        cursor.execute("INSERT INTO bak_basic (ts_code, trade_date, profit_yoy) VALUES (?, ?, ?)", (ts_code, LATEST_DATE, growth_val))
        cursor.execute("""
            INSERT INTO moneyflow (ts_code, trade_date, buy_sm_amount, sell_sm_amount, buy_md_amount, sell_md_amount, buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount) 
            VALUES (?, ?, 1000.0, 1000.0, 1000.0, 1000.0, 3000.0, 1000.0, 5000.0, 1000.0)
        """, (ts_code, LATEST_DATE))
        cursor.execute("INSERT INTO stk_holdernumber (ts_code, ann_date, end_date, holder_num) VALUES (?, ?, ?, 90000)", (ts_code, LATEST_DATE, LATEST_DATE))
        cursor.execute("INSERT INTO stk_holdernumber (ts_code, ann_date, end_date, holder_num) VALUES (?, ?, ?, 100000)", (ts_code, DATES[0], DATES[0]))

    raw_conn.commit()

def tear_down_mock_db():
    global original_get_conn
    if original_get_conn is not None:
        dao.get_conn = original_get_conn

def print_result(result):
    print(f"【整体流程状态】: {result['total_flow_status']}")
    if result["terminate_reason"]:
        print(f"【终止原因】: {result['terminate_reason']}")

    print("\n【宏观层诊断摘要】")
    macro = result["macro_summary"]
    print(f"  操作模式: {macro['operate_mode']} | 综合总分: {macro['total_score']}")
    print(f"  触发否决: {macro['veto_trigger']} | 修正明细: {macro['revise_desc']}")

    print("\n【板块层分析摘要】")
    board = result["board_summary"]
    print(f"  主流风格: {board['main_style']} | 轮动强度: {board['rotate_strength']}")
    print(f"  资金虹吸: {board['siphon_level']} | 市场仓位: {board['market_position']}")

    print("\n【标准化最终交易指令】")
    for trade in result["final_trade_list"]:
        print(f"  标的: {trade['stock_code']} {trade['stock_name']} | 评级: {trade['stock_level']}")
        print(f"    入场区: {trade['entry_zone']} | 支撑: {trade['support_price']} | 压力: {trade['pressure_price']}")
        print(f"    止损: {trade['stop_loss_price']} | 止盈1/2: {trade['first_profit_price']}/{trade['second_profit_price']}")
        print(f"    建议仓位: {trade['suggest_position']:.2%} | 风控提示: {trade['risk_tip']}")
    if not result["final_trade_list"]:
        print("  (无个股交易指令输出)")

    if result["global_risk_list"]:
        print("\n【全局风控风险列表】")
        for risk in result["global_risk_list"]:
            print(f"  - {risk}")

    if result["all_data_missing"]:
        print("\n【全链路数据缺失汇总】")
        for miss in result["all_data_missing"]:
            print(f"  - {miss}")
    print()

def test_normal():
    print("=================== 场景 1：正常行情全流程跑通验证 ===================")
    setup_base_mock_db()
    try:
        # 盘前预判
        pre_expect = {
            "up_down_ratio": 1.2,
            "top_board_change": 2.5,
            "limit_up_num": 25
        }
        res = total_workflow.run(pre_expect=pre_expect)
        print_result(res)
    finally:
        tear_down_mock_db()

def test_veto_terminate():
    print("=================== 场景 2：第一层宏观一票否决中途拦截验证 ===================")
    # 模拟流动性一票否决 (大额流出，且成交额骤降)
    setup_base_mock_db(is_liquidity_veto=True)
    try:
        res = total_workflow.run()
        print_result(res)
    finally:
        tear_down_mock_db()

def test_data_missing():
    print("=================== 场景 3：历史行情缺失与保守风控降级验证 ===================")
    # is_missing_prices = True 代表只有 5 天行情，导致历史 20 日不足
    setup_base_mock_db(is_missing_prices=True)
    try:
        res = total_workflow.run()
        print_result(res)
    finally:
        tear_down_mock_db()

def test_global_exception():
    print("=================== 场景 4：模块严重崩溃全局捕获与容错处理验证 ===================")
    setup_base_mock_db()
    
    # 故意破坏一个底层依赖方法，模拟触发抛错异常
    original_macro_run = macro_score.run
    macro_score.run = lambda: exec('raise Exception("模拟数据库底层连接完全中断断网严重故障")')
    
    try:
        res = total_workflow.run()
        print_result(res)
    finally:
        macro_score.run = original_macro_run
        tear_down_mock_db()

def main():
    print("🛸 开始进行《三层量化决策框架》全链路顶层总调度集成测试...")
    test_normal()
    test_veto_terminate()
    test_data_missing()
    test_global_exception()
    print("🏁 集成测试结束！")

if __name__ == "__main__":
    main()

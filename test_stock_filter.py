# -*- coding: utf-8 -*-
# test_stock_filter.py
import sqlite3
import pandas as pd
from db.dao import dao
from decision_framework.stock_filter import stock_filter
from decision_framework.board_link_siphon import board_link_siphon

# 模拟交易日序列
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

def setup_mock_db():
    """
    配置隔离的内存数据库并注入个股数据和行情序列以供全面测试
    """
    global original_get_conn, mem_conn
    original_get_conn = dao.get_conn
    
    # 建立内存连接并隔离
    raw_conn = sqlite3.connect(":memory:")
    dao.get_conn = lambda: MemConnWrapper(raw_conn)
    
    cursor = raw_conn.cursor()
    # 创建表结构
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
            ts_code TEXT, trade_date TEXT, turnover_rate REAL, volume_ratio REAL, pe REAL, pb REAL, ps REAL, total_share REAL, float_share REAL, free_share REAL, total_mv REAL, circ_mv REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS board_money_flow (
            board_name TEXT, trade_date TEXT, net_amount REAL, limit_up_count INTEGER,
            leader_height INTEGER, tier_complete INTEGER, year_rise REAL, historical_match INTEGER,
            flow_5d REAL, cover_ratio REAL, tier_status TEXT, sentry_status TEXT, retreat_ratio REAL, week_rise REAL
        )
    """)
    
    # 注入板块：半导体，让其流入资金排第一 (5亿)
    cursor.execute("""
        INSERT INTO board_money_flow (board_name, trade_date, net_amount, limit_up_count, leader_height, tier_complete, year_rise, historical_match, flow_5d, cover_ratio, tier_status, sentry_status, retreat_ratio, week_rise)
        VALUES ('半导体', ?, 500000000.0, 5, 4, 1, 0.10, 1, 500000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)
    """, (LATEST_DATE,))
    
    # 注入测试个股定义
    stocks_meta = [
        ("000001.SZ", "半导体A", "半导体"),  # 满足所有条件
        ("000002.SZ", "半导体B", "半导体"),  # 股价低于MA20
        ("000003.SZ", "半导体C", "半导体"),  # 换手率超上限 (20%)
        ("000004.SZ", "半导体D", "半导体"),  # 阶段累计涨幅超上限 (50%)
        ("000005.SZ", "半导体E", "半导体"),  # 成交量放大不足
        ("000006.SZ", "半导体F", "半导体")   # 关键数据缺失 (最新一天换手率为空)
    ]
    for ts_code, name, industry in stocks_meta:
        cursor.execute("INSERT INTO stock_list (ts_code, name, industry, list_date) VALUES (?, ?, ?, '20200101')", (ts_code, name, industry))
    
    # 注入 20 天历史行情数据
    for i, dt in enumerate(DATES):
        # 1. 000001.SZ (全部满足): 价格从 10.0 温和升到 12.0；最后一天成交量放大 (1500 VS 1000)；最新换手率 5.0 (即 5% = 0.05)
        close_1 = 10.0 + (i * 0.1) # 10.0 -> 11.9
        if dt == LATEST_DATE:
            close_1 = 12.0
            vol_1 = 1500.0
            turnover_1 = 5.0
        else:
            vol_1 = 1000.0
            turnover_1 = 3.0
        cursor.execute("INSERT INTO daily_prices (ts_code, trade_date, close, vol) VALUES ('000001.SZ', ?, ?, ?)", (dt, close_1, vol_1))
        cursor.execute("INSERT INTO daily_basic (ts_code, trade_date, turnover_rate) VALUES ('000001.SZ', ?, ?)", (dt, turnover_1))

        # 2. 000002.SZ (低于MA20): 最新收盘价大幅下跌，最新价格 9.0，而均线在 10 左右
        close_2 = 11.0 - (i * 0.05) # 11.0 -> 10.0
        if dt == LATEST_DATE:
            close_2 = 8.0
            vol_2 = 1000.0
            turnover_2 = 4.0
        else:
            vol_2 = 1000.0
            turnover_2 = 3.0
        cursor.execute("INSERT INTO daily_prices (ts_code, trade_date, close, vol) VALUES ('000002.SZ', ?, ?, ?)", (dt, close_2, vol_2))
        cursor.execute("INSERT INTO daily_basic (ts_code, trade_date, turnover_rate) VALUES ('000002.SZ', ?, ?)", (dt, turnover_2))

        # 3. 000003.SZ (换手率超上限): 最新一天换手率高达 20.0%
        close_3 = 10.0 + (i * 0.05)
        if dt == LATEST_DATE:
            close_3 = 11.0
            vol_3 = 1500.0
            turnover_3 = 20.0
        else:
            vol_3 = 1000.0
            turnover_3 = 3.0
        cursor.execute("INSERT INTO daily_prices (ts_code, trade_date, close, vol) VALUES ('000003.SZ', ?, ?, ?)", (dt, close_3, vol_3))
        cursor.execute("INSERT INTO daily_basic (ts_code, trade_date, turnover_rate) VALUES ('000003.SZ', ?, ?)", (dt, turnover_3))

        # 4. 000004.SZ (累计涨幅超标): 价格从 10.0 涨到 15.0，涨幅达 50%
        close_4 = 10.0 + (i * 0.25) # 10.0 -> 15.0
        if dt == LATEST_DATE:
            close_4 = 15.0
            vol_4 = 1500.0
            turnover_4 = 5.0
        else:
            vol_4 = 1000.0
            turnover_4 = 3.0
        cursor.execute("INSERT INTO daily_prices (ts_code, trade_date, close, vol) VALUES ('000004.SZ', ?, ?, ?)", (dt, close_4, vol_4))
        cursor.execute("INSERT INTO daily_basic (ts_code, trade_date, turnover_rate) VALUES ('000004.SZ', ?, ?)", (dt, turnover_4))

        # 5. 000005.SZ (放量不足): 成交量保持 1000，最新一天也是 1000 (要求比 20日均值 1.2倍，这里只有 1.0倍)
        close_5 = 10.0 + (i * 0.1)
        if dt == LATEST_DATE:
            close_5 = 12.0
            vol_5 = 1000.0
            turnover_5 = 5.0
        else:
            vol_5 = 1000.0
            turnover_5 = 3.0
        cursor.execute("INSERT INTO daily_prices (ts_code, trade_date, close, vol) VALUES ('000005.SZ', ?, ?, ?)", (dt, close_5, vol_5))
        cursor.execute("INSERT INTO daily_basic (ts_code, trade_date, turnover_rate) VALUES ('000005.SZ', ?, ?)", (dt, turnover_5))

        # 6. 000006.SZ (数据缺失): 最新一天的换手率为 None
        close_6 = 10.0 + (i * 0.1)
        if dt == LATEST_DATE:
            close_6 = 12.0
            vol_6 = 1500.0
            turnover_6 = None
        else:
            vol_6 = 1000.0
            turnover_6 = 3.0
        cursor.execute("INSERT INTO daily_prices (ts_code, trade_date, close, vol) VALUES ('000006.SZ', ?, ?, ?)", (dt, close_6, vol_6))
        cursor.execute("INSERT INTO daily_basic (ts_code, trade_date, turnover_rate) VALUES ('000006.SZ', ?, ?)", (dt, turnover_6))

    raw_conn.commit()
    print("[MockDB] 内存隔离数据库及 Mock 数据配置完成！")

def tear_down_mock_db():
    """
    清除 Mock 状态，还原真实数据库连接
    """
    global original_get_conn
    if original_get_conn is not None:
        dao.get_conn = original_get_conn
        print("[MockDB] 内存隔离数据库已卸载，真实连接已还原。")

def run_test_case(title: str, mock=False):
    print(f"\n===== {title} =====")
    if mock:
        setup_mock_db()
        
    try:
        # 1. 获取第二层最终板块结果
        board_result = board_link_siphon.run()
        # 2. 执行个股批量初筛
        filter_res = stock_filter.run(board_result)

        # 打印筛选板块
        print(f"\n【筛选板块】: {filter_res['filter_board']}")

        # 打印备选个股
        print("\n✅ 备选个股池 (候选个股)：")
        for stock in filter_res["candidate_stocks"]:
            print(f"代码:{stock['stock_code']} 名称:{stock['stock_name']} 判定明细:")
            for detail in stock['check_detail']:
                print(f"  - {detail}")

        # 打印排除个股
        print("\n❌ 被排除个股：")
        for stock in filter_res["exclude_stocks"]:
            print(f"代码:{stock['stock_code']} 名称:{stock['stock_name']} 排除原因:{stock['exclude_reason']}")

        # 打印数据缺失项
        if filter_res["data_missing_list"]:
            print(f"\n⚠️ 数据缺失项: {filter_res['data_missing_list']}")

        print(f"\n【流程状态】: {filter_res['flow_status']}")
        
    finally:
        if mock:
            tear_down_mock_db()

def main():
    print("🚀 开始进行个股初筛核心逻辑全场景自测...")
    
    # 场景 1：真实数据运行测试 (检查数据库是否由于无数据而降级)
    run_test_case("测试场景 1：真实数据库环境测试 (无特定 Mock 注入)", mock=False)
    
    # 场景 2：隔离 Mock 数据环境测试 (全判定场景覆盖验证)
    run_test_case("测试场景 2：隔离 Mock 环境测试 (全量业务条件覆盖)", mock=True)

if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
# test_stock_trade_risk.py
import sqlite3
import pandas as pd
from db.dao import dao
from decision_framework.stock_filter import stock_filter
from decision_framework.stock_score import stock_score
from decision_framework.stock_trade_risk import stock_trade_risk
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
    配置隔离的内存数据库并注入风控仓位测试所需的指标与价格历史
    """
    global original_get_conn, mem_conn
    original_get_conn = dao.get_conn
    
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
            ts_code TEXT, trade_date TEXT, pe REAL, turnover_rate REAL, volume_ratio REAL, free_share REAL
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

    # 1. 注入板块：半导体，属于“科技”风格，设置为强势板块 (流入5亿)
    cursor.execute("""
        INSERT INTO board_money_flow (board_name, trade_date, net_amount, limit_up_count, leader_height, tier_complete, year_rise, historical_match, flow_5d, cover_ratio, tier_status, sentry_status, retreat_ratio, week_rise)
        VALUES ('半导体', ?, 500000000.0, 5, 4, 1, 0.10, 1, 500000000.0, 0.12, '完整', '未消耗', 0.10, 0.01)
    """, (LATEST_DATE,))

    # 2. 注入 4 只个股定义
    stocks_meta = [
        ("000001.SZ", "半导体A", "半导体"),  # 优选入场区 + 优质标的
        ("000002.SZ", "半导体B", "半导体"),  # 谨慎入场区 + 良好标的
        ("000003.SZ", "半导体C", "半导体"),  # 禁入区 + 一般标的
        ("000004.SZ", "半导体D", "半导体")   # 价格历史天数缺失标的
    ]
    for ts_code, name, industry in stocks_meta:
        cursor.execute("INSERT INTO stock_list (ts_code, name, industry, list_date) VALUES (?, ?, ?, '20200101')", (ts_code, name, industry))

    # 3. 注入 20 天历史行情数据，控制其支撑与压力位
    # 000001.SZ (优选入场区): 支撑 10.0, 压力 15.0。最新收盘 10.2 (涨幅 2% <= 25%, 靠近支撑 2% <= 3%)
    # 000002.SZ (谨慎入场区): 支撑 10.0, 压力 15.0。最新收盘 12.5 (前日收盘 10.5, 涨幅 19% <= 25%)
    # 000003.SZ (禁入区): 支撑 10.0, 压力 15.0。最新收盘 14.8 (前日收盘 12.0, 涨幅 23.3% <= 25%, 靠近压力 1.3% <= 4%)
    for dt in DATES:
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
                
            cursor.execute("INSERT INTO daily_prices (ts_code, trade_date, open, high, low, close, vol) VALUES (?, ?, 11.0, ?, ?, ?, ?)", (ts_code, dt, high_val, low_val, close_val, vol_val))
            cursor.execute("INSERT INTO daily_basic (ts_code, trade_date, pe, turnover_rate) VALUES (?, ?, 15.0, ?)", (ts_code, dt, turnover_val))

    # 为 000004.SZ 仅注入 5 天历史数据以模拟缺失
    for dt in DATES[-5:]:
        cursor.execute("INSERT INTO daily_prices (ts_code, trade_date, open, high, low, close, vol) VALUES ('000004.SZ', ?, 11.0, 15.0, 10.0, 12.5, 1000.0)", (dt,))
        cursor.execute("INSERT INTO daily_basic (ts_code, trade_date, pe, turnover_rate) VALUES ('000004.SZ', ?, 15.0, 5.0)", (dt,))

    # 4. 为前置评分模块注入评级数据
    # 半导体A: 业绩增速 45% (优质)
    cursor.execute("INSERT INTO bak_basic (ts_code, trade_date, profit_yoy) VALUES ('000001.SZ', ?, 45.0)", (LATEST_DATE,))
    cursor.execute("INSERT INTO moneyflow (ts_code, trade_date, buy_sm_amount, sell_sm_amount, buy_md_amount, sell_md_amount, buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount) VALUES ('000001.SZ', ?, 1000.0, 1000.0, 1000.0, 1000.0, 3000.0, 1000.0, 5000.0, 1000.0)", (LATEST_DATE,))
    cursor.execute("INSERT INTO stk_holdernumber (ts_code, ann_date, end_date, holder_num) VALUES ('000001.SZ', '20260617', '20260617', 90000)")
    cursor.execute("INSERT INTO stk_holdernumber (ts_code, ann_date, end_date, holder_num) VALUES ('000001.SZ', '20260331', '20260331', 100000)")

    # 半导体B: 业绩增速 20% (良好)
    cursor.execute("INSERT INTO bak_basic (ts_code, trade_date, profit_yoy) VALUES ('000002.SZ', ?, 20.0)", (LATEST_DATE,))
    cursor.execute("INSERT INTO moneyflow (ts_code, trade_date, buy_sm_amount, sell_sm_amount, buy_md_amount, sell_md_amount, buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount) VALUES ('000002.SZ', ?, 7000.0, 7000.0, 1500.0, 1500.0, 1000.0, 1000.0, 500.0, 500.0)", (LATEST_DATE,))
    cursor.execute("INSERT INTO stk_holdernumber (ts_code, ann_date, end_date, holder_num) VALUES ('000002.SZ', '20260617', '20260617', 98000)")
    cursor.execute("INSERT INTO stk_holdernumber (ts_code, ann_date, end_date, holder_num) VALUES ('000002.SZ', '20260331', '20260331', 100000)")

    # 半导体C: 业绩增速表无数据，资金小流出 (一般)
    cursor.execute("INSERT INTO moneyflow (ts_code, trade_date, buy_sm_amount, sell_sm_amount, buy_md_amount, sell_md_amount, buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount) VALUES ('000003.SZ', ?, 9000.0, 9000.0, 800.0, 800.0, 100.0, 200.0, 100.0, 200.0)", (LATEST_DATE,))

    # 半导体D: 天数不足 (初筛因为数据天数小于20日，会在 stock_filter.run 时被作为缺失排除，所以它不会出现在被打分列表里)

    raw_conn.commit()
    print("[MockDB] 内存隔离数据库风控 Mock 数据配置完成！")

def tear_down_mock_db():
    global original_get_conn
    if original_get_conn is not None:
        dao.get_conn = original_get_conn
        print("[MockDB] 内存隔离数据库已卸载。")

def test_scenario(title: str, mock_mode: str = "进攻"):
    print(f"\n=================== {title} (当前宏观模式: {mock_mode}) ===================")
    setup_mock_db()
    
    try:
        # 1. 运行上层前置链条
        board_res = board_link_siphon.run()
        filter_res = stock_filter.run(board_res)
        score_res = stock_score.run(filter_res)
        
        # 手动注入一个缺少价格历史的个股，用于测试数据缺失降级机制
        score_res["stock_score_list"].append({
            "stock_code": "000004.SZ",
            "stock_name": "半导体D",
            "stock_level": "一般标的"
        })
        
        # 2. 运行子模块3
        trade_res = stock_trade_risk.run(score_res, board_res, mock_mode)

        # 打印交易风控明细
        print("\n【个股交易风控明细】")
        for item in trade_res["trade_list"]:
            print(f"标的: {item['stock_code']} {item['stock_name']}")
            print(f"  入场分区: {item['entry_zone']}")
            print(f"  强支撑位: {item['support_price']} | 短期压力位: {item['pressure_price']}")
            print(f"  固定止损价: {item['stop_loss_price']} | 第一/二止盈价: {item['first_profit_price']}/{item['second_profit_price']}")
            print(f"  建议仓位: {item['suggest_position']:.2%} | 系数明细: {item['position_detail']}")
            print(f"  跟踪规则: {item['trailing_rule']}")
            print()

        # 全局风控提示 & 缺失列表
        print(f"【全局风控提示】: {trade_res['global_risk_tip']}")
        if trade_res["data_missing_list"]:
            print(f"⚠️ 数据缺失项: {trade_res['data_missing_list']}")
        print(f"【流程状态】: {trade_res['flow_status']}")
        
    finally:
        tear_down_mock_db()

def main():
    print("🚀 开始进行个股入场/止损止盈/动态仓位分配核心逻辑全场景自测...")
    
    # 场景 1：进攻模式测试 (验证点位分区与正常点位计算)
    test_scenario("测试场景 1：正常环境测试 (宏观进攻模式)", mock_mode="进攻")
    
    # 场景 2：防守模式测试 (验证止损止盈收窄与仓位降配)
    test_scenario("测试场景 2：极端风控测试 (宏观防守模式)", mock_mode="防守")

if __name__ == "__main__":
    main()

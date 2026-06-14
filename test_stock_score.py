# -*- coding: utf-8 -*-
# test_stock_score.py
import sqlite3
import pandas as pd
from db.dao import dao
from decision_framework.stock_filter import stock_filter
from decision_framework.stock_score import stock_score
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
    配置隔离的内存数据库并注入打分测试所需的指标数据
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

    # 1. 注入板块：半导体
    cursor.execute("""
        INSERT INTO board_money_flow (board_name, trade_date, net_amount, limit_up_count, leader_height, tier_complete, year_rise, historical_match, flow_5d, cover_ratio, tier_status, sentry_status, retreat_ratio, week_rise)
        VALUES ('半导体', ?, 500000000.0, 5, 4, 1, 0.10, 1, 500000000.0, 0.10, '完整', '未消耗', 0.10, 0.01)
    """, (LATEST_DATE,))

    # 2. 注入 4 只个股定义
    stocks_meta = [
        ("000001.SZ", "半导体A", "半导体"),  # 优异标的 (各项满分)
        ("000002.SZ", "半导体B", "半导体"),  # 一般标的 (各项中性)
        ("000003.SZ", "半导体C", "半导体"),  # 缺失指标较多
        ("000004.SZ", "半导体D", "半导体")   # 触发程序运行异常个股
    ]
    for ts_code, name, industry in stocks_meta:
        cursor.execute("INSERT INTO stock_list (ts_code, name, industry, list_date) VALUES (?, ?, ?, '20200101')", (ts_code, name, industry))

    # 3. 注入 20 天日线数据 (让其通过前置初筛)
    for ts_code, _, _ in stocks_meta:
        for dt in DATES:
            # 保证初筛均线和放量通过
            close_val = 12.0 if dt == LATEST_DATE else 10.0
            vol_val = 1500.0 if dt == LATEST_DATE else 1000.0
            turnover_val = 5.0
            
            cursor.execute("INSERT INTO daily_prices (ts_code, trade_date, close, vol) VALUES (?, ?, ?, ?)", (ts_code, dt, close_val, vol_val))
            cursor.execute("INSERT INTO daily_basic (ts_code, trade_date, pe, turnover_rate) VALUES (?, ?, 15.0, ?)", (ts_code, dt, turnover_val))

    # ==================== 个股打分定制化数据注入 ====================

    # --- 个股 1: 000001.SZ (全部优秀) ---
    # 基本面: 业绩增速 45% (>=30% 1.0分)
    cursor.execute("INSERT INTO bak_basic (ts_code, trade_date, profit_yoy) VALUES ('000001.SZ', ?, 45.0)", (LATEST_DATE,))
    # 估值分位: 历史 750 天 PE，最新 PE 9.0，历史大部分为 15.0 -> 最新处于估值极低分位 (<=20% 1.0分)
    cursor.execute("UPDATE daily_basic SET pe = 9.0 WHERE ts_code = '000001.SZ' AND trade_date = ?", (LATEST_DATE,))
    # 资金面: 主力占比 50% (>=3% 1.0分)，大单成交占比 30% (>=20% 1.0分)
    # buy_sm_amount, sell_sm_amount, buy_md_amount, sell_md_amount, buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount
    cursor.execute("""
        INSERT INTO moneyflow (ts_code, trade_date, buy_sm_amount, sell_sm_amount, buy_md_amount, sell_md_amount, buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount)
        VALUES ('000001.SZ', ?, 1000.0, 1000.0, 1000.0, 1000.0, 3000.0, 1000.0, 5000.0, 1000.0)
    """, (LATEST_DATE,))
    # 筹码集中度: 最新一期户数 90000，上一期 100000，变化率 -10% (<= -5% 1.0分)
    cursor.execute("INSERT INTO stk_holdernumber (ts_code, ann_date, end_date, holder_num) VALUES ('000001.SZ', '20260617', '20260617', 90000)")
    cursor.execute("INSERT INTO stk_holdernumber (ts_code, ann_date, end_date, holder_num) VALUES ('000001.SZ', '20260331', '20260331', 100000)")
    # 换手稳定性: 5天换手均为 5.0，变异系数 0% (<20% 1.0分)
    # 上面日线循环已注入 5.0

    # --- 个股 2: 000002.SZ (全部中性) ---
    # 基本面: 业绩增速 20% (10%~30% 0.5分)
    cursor.execute("INSERT INTO bak_basic (ts_code, trade_date, profit_yoy) VALUES ('000002.SZ', ?, 20.0)", (LATEST_DATE,))
    # 估值分位: 历史 750 天 PE，最新 15.0，历史有高有低 (50%分位数 0.5分)
    # 资金面: 主力占比 1.5% (0.5分)，大单成交占比 15% (0.5分)
    # sm_in=2000, sm_out=2000, md_in=2000, md_out=2000, lg_in=800, lg_out=800, elg_in=800, elg_out=600.
    # net_main = (800+800) - (800+600) = 200. total_buy = 2000+2000+800+800 = 5600. net_ratio = 200/5600 = 3.5%...
    # 我们调一下: sm=3000, md=3000, lg=1000, elg=1000, lg_out=1000, elg_out=900.
    # net_main = (1000+1000) - (1000+900) = 100. total_buy = 3000+3000+1000+1000 = 8000. 100/8000 = 1.25% (0.5分)
    # big_flow = 1000+1000+1000+900 = 3900. total_flow_sum = 3000*2+3000*2+1000*2+1000+900 = 15900. 3900/15900 = 24.5%...
    # 我们精准设计:
    # lg_in=1000, lg_out=1000, elg_in=500, elg_out=400 (net_main = 100)
    # total_flow_sum = 10000 (sm_in+sm_out=5000, md_in+md_out=2100, lg_in+lg_out=2000, elg_in+elg_out=900)
    # big_flow = 2900. big_ratio = 2900/10000 = 29% -> 等于 1.0分了。
    # 重设：total_flow_sum = 20000. big_flow = 3000 (lg_in=1000, lg_out=1000, elg_in=500, elg_out=500). big_ratio = 15% (0.5分)
    # net_main = (500+1000) - (500+900) = 100. total_buy = sm_in(7000) + md_in(1500) + lg_in(1000) + elg_in(500) = 10000. net_ratio = 100/10000 = 1.0% (0.5分)
    cursor.execute("""
        INSERT INTO moneyflow (ts_code, trade_date, buy_sm_amount, sell_sm_amount, buy_md_amount, sell_md_amount, buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount)
        VALUES ('000002.SZ', ?, 7000.0, 7000.0, 1500.0, 1500.0, 1000.0, 1000.0, 500.0, 500.0)
    """, (LATEST_DATE,))
    # 筹码集中度: 最新一期户数 98000，上一期 100000，变化率 -2% (-5%~0% 0.5分)
    cursor.execute("INSERT INTO stk_holdernumber (ts_code, ann_date, end_date, holder_num) VALUES ('000002.SZ', '20260617', '20260617', 98000)")
    cursor.execute("INSERT INTO stk_holdernumber (ts_code, ann_date, end_date, holder_num) VALUES ('000002.SZ', '20260331', '20260331', 100000)")
    # 换手稳定性: 换手变化从 5.0 -> 3.5 -> 5.0 -> 6.5 -> 5.0 (CV 在 20%~40% 区间，0.5分)
    for i, dt in enumerate(DATES[-5:]):
        val = [5.0, 3.5, 5.0, 6.5, 5.0][i]
        cursor.execute("UPDATE daily_basic SET turnover_rate = ? WHERE ts_code = '000002.SZ' AND trade_date = ?", (val, dt))

    # --- 个股 3: 000003.SZ (数据缺失) ---
    # 基本面: 业绩增速表缺失任何记录 (业绩取中性0.5)
    # 估值分位: 估值最新一天PE过高 (35.0, 分位分 0.0)
    cursor.execute("UPDATE daily_basic SET pe = 35.0 WHERE ts_code = '000003.SZ' AND trade_date = ?", (LATEST_DATE,))
    # 资金面: 主力流出 (net_ratio < 0 -> 0分)，大单成交偏少 (<10% -> 0分)
    cursor.execute("""
        INSERT INTO moneyflow (ts_code, trade_date, buy_sm_amount, sell_sm_amount, buy_md_amount, sell_md_amount, buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount)
        VALUES ('000003.SZ', ?, 9000.0, 9000.0, 800.0, 800.0, 100.0, 200.0, 100.0, 200.0)
    """, (LATEST_DATE,))
    # 筹码集中度: 股东户数缺失记录 (筹码取 0.5)
    # 换手稳定性: 换手率包含 None (缺失，取 0.5)
    cursor.execute("UPDATE daily_basic SET turnover_rate = NULL WHERE ts_code = '000003.SZ' AND trade_date = ?", (LATEST_DATE,))

    # --- 个股 4: 000004.SZ (故意构造异常) ---
    # 我们不为 000004.SZ 在 moneyflow 中插入数据，但我们也不让它在 `calc_single_index` 里抛出异常。
    # 为验证异常，我们可以针对 000004.SZ 跑一个会出错的操作：
    # 比如在 `stk_holdernumber` 中插入包含非数字的错误字符，或者在打分时如果检测到 000004.SZ 则故意除以零。
    # 让我们来看 `stock_score.py`：对运行发生异常的个股，在 `batch_score` 里通过 try-except 完美捕获，降级评为偏弱。
    # 我们在此通过把 `000004.SZ` 的历史 pe 设为字符串 'invalid_pe' 导致转换 float 异常！
    cursor.execute("UPDATE daily_basic SET pe = 'invalid_pe' WHERE ts_code = '000004.SZ'")

    raw_conn.commit()
    print("[MockDB] 内存隔离数据库打分 Mock 数据配置完成！")

def tear_down_mock_db():
    global original_get_conn
    if original_get_conn is not None:
        dao.get_conn = original_get_conn
        print("[MockDB] 内存隔离数据库已卸载。")

def run_test_case(title: str, mock=False):
    print(f"\n===== {title} =====")
    if mock:
        setup_mock_db()
        
    try:
        # 1. 获取第一层板块与第二层初筛个股
        board_result = board_link_siphon.run()
        filter_result = stock_filter.run(board_result)
        
        # 2. 执行个股批量多维度评分
        score_result = stock_score.run(filter_result)

        # 打印打分评级明细
        print("\n【个股打分 & 评级明细】")
        for item in score_result["stock_score_list"]:
            print(f"代码: {item['stock_code']} 名称: {item['stock_name']}")
            print(f"  基本面: {item['fundamental_score']:.2f} | 资金面: {item['capital_score']:.2f} | 筹码结构: {item['chip_score']:.2f}")
            print(f"  综合总分: {item['total_score']:.2f} | 综合评级: {item['stock_level']}")
            print("  指标明细:")
            for desc in item['detail']:
                print(f"    - {desc}")
            print()

        # 打印数据缺失 & 异常个股
        if score_result["data_missing_list"]:
            print(f"⚠️ 数据缺失项: {score_result['data_missing_list']}")
        if score_result["abnormal_stock_list"]:
            print(f"🚨 数据异常个股: {score_result['abnormal_stock_list']}")

        print(f"\n【流程状态】: {score_result['flow_status']}")
        
    finally:
        if mock:
            tear_down_mock_db()

def main():
    print("🚀 开始进行个股多维度量化打分全场景自测...")
    
    # 场景 1：真实数据运行测试 (无 Mock)
    run_test_case("测试场景 1：真实数据库环境测试 (无特定 Mock 注入)", mock=False)
    
    # 场景 2：隔离 Mock 数据环境测试 (验证计分、加权、缺失值补齐、异常容错与评级划分)
    run_test_case("测试场景 2：隔离 Mock 环境测试 (全量打分场景验证)", mock=True)

if __name__ == "__main__":
    main()

# test_board_structure.py
import sqlite3
from db.dao import dao
from decision_framework.board_structure import board_structure
from decision_framework.board_rank import board_rank


def setup_mock_data():
    """
    为第二层“板块结构与中军判定”注入高完整性的测试数据，涵盖以下情况：
    
    1. 第一层配置（board_money_flow & daily_prices）：
       - 注入昨日主力流入的行业板块，使 board_rank.run() 筛出观察板块: '半导体'、'软件'
    
    2. 个股映射（stock_list）：
       - 半导体成分股：'000001.SZ'（中军锚）、'000002.SZ'（连板股）、'000003.SZ'（跟风股）
       - 软件成分股：'000004.SZ'（中军锚）、'000005.SZ'（首板股）
       
    3. 个股交易历史（daily_prices，日期 20260613 及前推）：
       - 半导体中军 (000001)：最新收盘红盘 (+1.5%)，成交额 30 亿。
       - 半导体龙头 (000002)：最新/前日/前前日连续涨停，共 3 连板 (龙头)。
       - 半导体跟风 (000003)：最新首板涨停，共 1 连板。
       - 软件中军 (000004)：最新绿盘 (-2.0%)，成交额 25 亿。
       - 软件首板 (000005)：最新首板涨停，共 1 连板。
       
    4. 个股基本面（daily_basic，自由流通盘）：
       - 000001 (半导体中军)：流通市值 1500000 万元 (150亿，满足 >= 50亿 门槛)。
       - 000004 (软件中军)：流通市值 2000000 万元 (200亿，满足 >= 50亿 门槛)。
    """
    print("\n[MockDB] 正在临时注入测试数据以供第二层板块结构验证...")
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
        # 写入半导体和软件两个强流入行业
        cursor.execute("INSERT INTO board_money_flow VALUES ('半导体', '20260613', 12000000000.0, 3, 4, 1, 0.20, 1, 12000000000.0, 0.15, '完整', '未消耗', 0.18, 0.02)")
        cursor.execute("INSERT INTO board_money_flow VALUES ('软件', '20260613', 8000000000.0, 2, 3, 1, 0.10, 1, 6000000000.0, 0.10, '基本完整', '未消耗', 0.16, 0.01)")
        
        # B. 注入 stock_list 成分股映射
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000001.SZ', '半导体中军', '半导体')")
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000002.SZ', '半导体龙头', '半导体')")
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000003.SZ', '半导体跟风', '半导体')")
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000004.SZ', '软件中军', '软件')")
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000005.SZ', '软件首板', '软件')")
        
        # C. 注入价格与涨幅历史 (20260613 - 今日，20260612 - 昨日，20260611 - 前日)
        # 板块 A (半导体) 总成交额 = 30亿 + 3亿 + 1亿 = 34亿
        # 中军 000001 成交 30 亿 (占比 30/34 = 88.2% >= 8% 中军门槛)，红盘
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000001.SZ', '20260613', 1.50, 3000000000.0, 15.0)")
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000001.SZ', '20260612', -0.50, 2000000000.0, 14.8)")
        
        # 龙头 000002 连续 3 天涨停
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000002.SZ', '20260613', 9.95, 300000000.0, 20.0)")
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000002.SZ', '20260612', 9.91, 250000000.0, 18.2)")
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000002.SZ', '20260611', 9.96, 200000000.0, 16.5)")
        
        # 跟风 000003 最新首涨停
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000003.SZ', '20260613', 9.92, 100000000.0, 10.0)")
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000003.SZ', '20260612', 0.20, 80000000.0, 9.10)")
        
        # 板块 B (软件) 总成交 = 25亿 + 2亿 = 27亿
        # 中军 000004 成交 25 亿 (占比 25/27 = 92.6% >= 8% 中军门槛)，绿盘 (-2.0%)
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000004.SZ', '20260613', -2.00, 2500000000.0, 30.0)")
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000004.SZ', '20260612', 1.00, 2000000000.0, 30.6)")
        
        # 首板 000005 最新涨停
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000005.SZ', '20260613', 9.90, 200000000.0, 8.00)")
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000005.SZ', '20260612', -2.30, 150000000.0, 7.28)")

        # D. 注入市值 (自由流通市值)
        cursor.execute("INSERT OR REPLACE INTO daily_basic (ts_code, trade_date, circ_mv) VALUES ('000001.SZ', '20260613', 1500000.0)") # 150亿
        cursor.execute("INSERT OR REPLACE INTO daily_basic (ts_code, trade_date, circ_mv) VALUES ('000004.SZ', '20260613', 2000000.0)") # 200亿
        
        # E. 第一层所依赖价格数据，确保打分不触发防守
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, amount) VALUES ('DUMMY_STOCK', '20260529', 1000000000.0)")

        conn.commit()
        print("[MockDB] 注入成功。")
    except Exception as e:
        print(f"[MockDB] 注入失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def teardown_mock_data():
    """
    清理临时注入的数据表及个股价格
    """
    print("\n[MockDB] 正在清理临时注入的第二层测试数据...")
    conn = dao.get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("DROP TABLE IF EXISTS board_money_flow")
        cursor.execute("DELETE FROM stock_list WHERE ts_code IN ('000001.SZ', '000002.SZ', '000003.SZ', '000004.SZ', '000005.SZ')")
        cursor.execute("DELETE FROM daily_prices WHERE ts_code IN ('000001.SZ', '000002.SZ', '000003.SZ', '000004.SZ', '000005.SZ', 'DUMMY_STOCK')")
        cursor.execute("DELETE FROM daily_basic WHERE ts_code IN ('000001.SZ', '000004.SZ')")
        conn.commit()
        print("[MockDB] 清理成功。")
    except Exception as e:
        print(f"[MockDB] 清理异常: {e}")
        conn.rollback()
    finally:
        conn.close()


def main():
    print("===== 第二层：板块梯队&中军结构 测试 =====")
    
    # 注入 mock 数据
    setup_mock_data()
    try:
        # 1. 获取第一层板块结果
        board_result = board_rank.run()
        if not board_result["board_list"]:
            print("无候选板块，流程终止")
            return

        # 2. 遍历板块，逐个判定结构
        for board in board_result["board_list"]:
            board_name = board["board_name"]
            print(f"\n---------- 板块：{board_name} ----------")
            res = board_structure.run(board_name)
            print(f"梯队评级: {res['ladder_rating']} | 原因: {res['ladder_reason']}")
            print(f"中军标的: {res['main_name']} | 中军评级: {res['main_rating']}")
            print(f"综合结构: {res['composite_rating']}")
            if res["data_missing_list"]:
                print(f"数据缺失项: {res['data_missing_list']}")
                
    finally:
        # 销毁测试数据，确保不影响后续大盘流程
        teardown_mock_data()


if __name__ == "__main__":
    main()

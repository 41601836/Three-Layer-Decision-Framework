# test_board_structure.py
import sqlite3
from db.dao import dao
from decision_framework.board_structure import board_structure
from decision_framework.board_rank import board_rank


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
    global original_get_conn, mem_conn
    print("\n[MockDB] 正在启用内存隔离数据库注入测试数据以供第二层板块结构验证...")
    original_get_conn = dao.get_conn

    raw_conn = sqlite3.connect(":memory:")
    cursor = raw_conn.cursor()
    try:
        # A. 创建测试需要的全部表结构
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS board_money_flow (
                board_name TEXT, trade_date TEXT, net_amount REAL, limit_up_count INTEGER,
                leader_height INTEGER, tier_complete INTEGER, year_rise REAL, historical_match INTEGER,
                flow_5d REAL, cover_ratio REAL, tier_status TEXT, sentry_status TEXT, retreat_ratio REAL, week_rise REAL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_list (
                ts_code TEXT, name TEXT, industry TEXT, list_date TEXT
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

        # B. 写入半导体和软件两个强流入行业
        cursor.execute("INSERT INTO board_money_flow VALUES ('半导体', '20260613', 12000000000.0, 3, 4, 1, 0.20, 1, 12000000000.0, 0.15, '完整', '未消耗', 0.18, 0.02)")
        cursor.execute("INSERT INTO board_money_flow VALUES ('软件', '20260613', 8000000000.0, 2, 3, 1, 0.10, 1, 6000000000.0, 0.10, '基本完整', '未消耗', 0.16, 0.01)")
        
        # C. 注入 stock_list 成分股映射
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000001.SZ', '半导体中军', '半导体')")
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000002.SZ', '半导体龙头', '半导体')")
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000003.SZ', '半导体跟风', '半导体')")
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000004.SZ', '软件中军', '软件')")
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000005.SZ', '软件首板', '软件')")
        
        # D. 注入价格与涨幅历史
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000001.SZ', '20260613', 1.50, 3000000000.0, 15.0)")
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000001.SZ', '20260612', -0.50, 2000000000.0, 14.8)")
        
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000002.SZ', '20260613', 9.95, 300000000.0, 20.0)")
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000002.SZ', '20260612', 9.91, 250000000.0, 18.2)")
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000002.SZ', '20260611', 9.96, 200000000.0, 16.5)")
        
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000003.SZ', '20260613', 9.92, 100000000.0, 10.0)")
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000003.SZ', '20260612', 0.20, 80000000.0, 9.10)")
        
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000004.SZ', '20260613', -2.00, 2500000000.0, 30.0)")
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000004.SZ', '20260612', 1.00, 2000000000.0, 30.6)")
        
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000005.SZ', '20260613', 9.90, 200000000.0, 8.00)")
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close) VALUES ('000005.SZ', '20260612', -2.30, 150000000.0, 7.28)")

        # E. 注入市值
        cursor.execute("INSERT OR REPLACE INTO daily_basic (ts_code, trade_date, circ_mv) VALUES ('000001.SZ', '20260613', 1500000.0)")
        cursor.execute("INSERT OR REPLACE INTO daily_basic (ts_code, trade_date, circ_mv) VALUES ('000004.SZ', '20260613', 2000000.0)")
        
        # F. 第一层所依赖价格数据，确保不触发防守
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, amount) VALUES ('DUMMY_STOCK', '20260529', 1000000000.0)")

        raw_conn.commit()
        mem_conn = MemConnWrapper(raw_conn)
        dao.get_conn = lambda: mem_conn
        print("[MockDB] 内存隔离数据库注入成功。")
    except Exception as e:
        print(f"[MockDB] 注入失败: {e}")
        if original_get_conn is not None:
            dao.get_conn = original_get_conn


def teardown_mock_data():
    """
    清理临时注入的内存隔离库，还原物理数据库连接
    """
    global original_get_conn, mem_conn
    print("\n[MockDB] 正在清理内存隔离数据库并恢复真实连接...")
    if original_get_conn is not None:
        dao.get_conn = original_get_conn
        original_get_conn = None
    mem_conn = None
    print("[MockDB] 恢复物理连接成功。")


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

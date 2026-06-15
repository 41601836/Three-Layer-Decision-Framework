# test_board_style.py
import sqlite3
from db.dao import dao
from decision_framework.board_style import board_style
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


def setup_mock_style_data():
    """
    为第二层风格判定注入 4 天的历史交易数据 (20260613、20260612、20260611、20260610)，
    以完整模拟日内/跨日风格热度校验。
    
    1. 注入 board_money_flow 数据使第一层筛选通过，得到 '半导体' 候选板块
    2. 科技板块成分股映射：
       - '000001.SZ' (半导体，大市值，大成交额)
       - '000002.SZ' (半导体，连板股)
    3. 写入 4 日交易历史：
       - 最新一日 (20260613)：
         000001.SZ 成交 30 亿，涨跌 +2.0%
         000002.SZ 涨停 (+9.95%)
         DUMMY_STOCK (全市场参照股) 成交 70 亿 (全市场总成交 = 100 亿，半导体占比 30% > 20%，成交满分)
       - 昨日 (20260612)、前日 (20260611)、大前日 (20260610)：
         均按上文比例写入，保证这 4 天科技风格在量能、涨停数和连板高度上都维持极高热度 (日内与跨日热度均强势)
    """
    global original_get_conn, mem_conn
    print("\n[MockDB] 正在启用内存隔离数据库并注入 4 天历史数据以供跨日热度验证...")
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

        # B. 注入 board_money_flow 数据
        cursor.execute("INSERT OR REPLACE INTO board_money_flow VALUES ('半导体', '20260613', 12000000000.0, 3, 4, 1, 0.20, 1, 12000000000.0, 0.15, '完整', '未消耗', 0.18, 0.02)")
        
        # C. 注入成份股
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000001.SZ', '半导体权重', '半导体')")
        cursor.execute("INSERT OR REPLACE INTO stock_list (ts_code, name, industry) VALUES ('000002.SZ', '半导体龙头', '半导体')")
        
        # D. 注入 4 天价格及成交额 (T, T-1, T-2, T-3)
        dates = ["20260613", "20260612", "20260611", "20260610"]
        for dt in dates:
            # 1. 中军 30 亿成交
            cursor.execute("""
                INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close)
                VALUES ('000001.SZ', ?, 2.0, 3000000000.0, 10.0)
            """, (dt,))
            
            # 2. 龙头股持续涨停
            cursor.execute("""
                INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close)
                VALUES ('000002.SZ', ?, 9.95, 200000000.0, 15.0)
            """, (dt,))
            
            # 3. 市场参照股 (分母，以计算风格成交额占比)
            # 注入大成交额以确保 market_total 为 100 亿，风格占比为 32%
            cursor.execute("""
                INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, pct_chg, amount, close)
                VALUES ('DUMMY_STOCK', ?, 0.5, 6800000000.0, 8.0)
            """, (dt,))
            
        raw_conn.commit()
        mem_conn = MemConnWrapper(raw_conn)
        dao.get_conn = lambda: mem_conn
        print("[MockDB] 内存隔离数据库注入成功。")
    except Exception as e:
        print(f"[MockDB] 注入失败: {e}")
        if original_get_conn is not None:
            dao.get_conn = original_get_conn


def teardown_mock_style_data():
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
    print("===== 第二层：风格划分 & 风格强度 测试 =====")
    
    # 注入 mock 数据以测试跨日延续性
    setup_mock_style_data()
    try:
        # 1. 获取上一层板块结果
        board_res = board_rank.run()
        # 2. 执行风格全流程
        style_res = board_style.run(board_res)

        # 打印风格分组&热度
        print("\n【风格分组 & 热度明细】")
        for item in style_res["style_group"]:
            print(f"风格: {item['style_name']}")
            print(f"  板块列表: {item['board_list']}")
            print(f"  日内得分: {item['intra_score']} | 日内强度: {item['intraday_strength']}")
            print(f"  跨日得分: {item['cross_score']} | 跨日强度: {item['cross_day_strength']}\n")

        # 打印缺失项
        if style_res["data_missing_list"]:
            print(f"【数据缺失项】: {style_res['data_missing_list']}")
            
    finally:
        # 无论成功失败均彻底清理测试数据
        teardown_mock_style_data()


if __name__ == "__main__":
    main()

# test_board_rank.py
import sqlite3
from db.dao import dao
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


def setup_mock_board_data():
    """
    往 SQLite 内存数据库中建表并注入 mock 板块资金流向记录，用于自测试。
    
    注入6个板块：
    1. 半导体 (A)：流入 120 亿，5日 120 亿。涨停 3，高度 4，完整。年涨幅 20%，回撤 18%，周涨幅 2%。中军未消耗。
       - 应通过初筛。总分：5.0。第一优先级。
    2. 软件 (B)：流入 40 亿，5日 60 亿。涨停 2，高度 3，基本完整。年涨幅 10%，回撤 16%，周涨幅 1%。中军消耗走强。
       - 应通过初筛。总分：3.0。第二优先级。
    3. 光伏 (C)：流入 30 亿，5日 20 亿。涨停 2，高度 3，断层。年涨幅 5%，回撤 12%，周涨幅 6%。中军消耗且断板。
       - 应通过初筛. 总分：0.0。第三优先级。
    4. 锂电 (D)：流入 8 亿。资金排第六，初筛截取前5时被过滤。
    5. 券商 (E)：流入 80 亿。年内涨幅 60%，超 50% 透支，初筛被剔除。
    6. 医药 (F)：流入 50 亿。涨停数 1，投机度不足，初筛被剔除。
    
    虹吸：半导体(120亿) / 软件(40亿) = 3.0 > 2.0 倍。将触发虹吸风险，锁定软件和光伏策略B暂停。
    """
    global original_get_conn, mem_conn
    print("\n[MockDB] 正在启用内存隔离数据库并注入测试数据...")
    original_get_conn = dao.get_conn

    raw_conn = sqlite3.connect(":memory:")
    cursor = raw_conn.cursor()
    try:
        # 1. 创建表结构
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS board_money_flow (
                board_name TEXT,
                trade_date TEXT,
                net_amount REAL,
                limit_up_count INTEGER,
                leader_height INTEGER,
                tier_complete INTEGER,
                year_rise REAL,
                historical_match INTEGER,
                flow_5d REAL,
                cover_ratio REAL,
                tier_status TEXT,
                sentry_status TEXT,
                retreat_ratio REAL,
                week_rise REAL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_prices (
                ts_code TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                pre_close REAL,
                change REAL,
                pct_chg REAL,
                vol REAL,
                amount REAL,
                adj_factor REAL
            )
        """)

        # 2. 插入6个测试板块
        mock_records = [
            ("半导体", "20260613", 12000000000.0, 3, 4, 1, 0.20, 1, 12000000000.0, 0.15, "完整", "未消耗", 0.18, 0.02),
            ("软件", "20260613", 4000000000.0, 2, 3, 1, 0.10, 1, 6000000000.0, 0.10, "基本完整", "消耗但次日走强", 0.16, 0.01),
            ("光伏", "20260613", 3000000000.0, 2, 3, 0, 0.05, 1, 2000000000.0, 0.05, "断层", "消耗且断板", 0.12, 0.06),
            ("锂电", "20260613", 800000000.0, 3, 4, 1, 0.15, 1, 5000000000.0, 0.10, "完整", "未消耗", 0.20, 0.02),
            ("券商", "20260613", 8000000000.0, 3, 4, 1, 0.60, 1, 9000000000.0, 0.15, "完整", "未消耗", 0.10, 0.02),
            ("医药", "20260613", 5000000000.0, 1, 2, 1, 0.12, 1, 4000000000.0, 0.05, "基本完整", "未消耗", 0.18, 0.02)
        ]

        cursor.executemany("""
            INSERT INTO board_money_flow (
                board_name, trade_date, net_amount, limit_up_count, leader_height, 
                tier_complete, year_rise, historical_match, flow_5d, cover_ratio, 
                tier_status, sentry_status, retreat_ratio, week_rise
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, mock_records)

        # 3. 注入对应价格表记录
        cursor.execute("INSERT OR REPLACE INTO daily_prices (ts_code, trade_date, amount) VALUES ('DUMMY_STOCK', '20260529', 1000000000.0)")

        raw_conn.commit()
        mem_conn = MemConnWrapper(raw_conn)
        dao.get_conn = lambda: mem_conn
        print("[MockDB] 内存隔离数据库注入成功。")
    except Exception as e:
        print(f"[MockDB] 注入失败: {e}")
        if original_get_conn is not None:
            dao.get_conn = original_get_conn


def teardown_mock_board_data():
    """
    清理临时创建的内存隔离库，还原物理数据库连接
    """
    global original_get_conn, mem_conn
    print("\n[MockDB] 开始清理内存隔离数据库并还原物理连接...")
    if original_get_conn is not None:
        dao.get_conn = original_get_conn
        original_get_conn = None
    mem_conn = None
    print("[MockDB] 恢复物理连接成功。")


def main():
    print("===== 板块初筛&优先级&资金虹吸 全流程测试 =====")
    
    # 注入测试数据
    setup_mock_board_data()
    try:
        result = board_rank.run()

        # 打印流程状态
        print(f"\n流程状态: {result['flow_status']}")

        # 打印板块列表
        print("\n【候选板块 & 优先级明细】")
        for item in result["board_list"]:
            print(f"板块名称: {item['board_name']}")
            print(f"综合总分: {item['total_score']}")
            print(f"优先级: {item['priority']}")
            print(f"策略规则: {item['strategy_rule']}\n")

        # 打印虹吸风险
        print(f"【资金虹吸风险】: {result['siphon_risk']}")
        if result["siphon_desc"]:
            print(f"风险说明: {result['siphon_desc']}")

        # 打印数据缺失项
        if result["data_missing_list"]:
            print(f"\n【数据缺失清单】: {result['data_missing_list']}")
            
    finally:
        # 清除临时数据，确保无脏数据残留
        teardown_mock_board_data()


if __name__ == "__main__":
    main()

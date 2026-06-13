# -*- coding: utf-8 -*-
# test_macro_veto.py
import sqlite3
from datetime import datetime, timedelta
from decision_framework.macro_veto import macro_veto

def setup_test_data():
    """注入测试用的完美模拟数据，使得各项否决不触发且健康度良性"""
    print("📥 [Setup] 正在向 SQLite 注入测试数据...")
    conn = sqlite3.connect('db/stock_daily.db')
    cursor = conn.cursor()
    
    # 0. 先确保所需的表全部存在，防范表缺失导致的报错
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_prices (
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            pct_chg REAL,
            vol REAL,
            amount REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hsgt_moneyflow (
            trade_date TEXT NOT NULL PRIMARY KEY,
            north_money REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS margin_detail (
            trade_date TEXT NOT NULL,
            ts_code TEXT NOT NULL,
            rzye REAL,
            PRIMARY KEY (trade_date, ts_code)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_index (
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            pct_chg REAL,
            vol REAL,
            amount REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
    """)

    # 1. 注入最近 5 日的 daily_prices 数据以保证成交额对比不触发流动性枯竭，且包含每日涨跌比数据
    cursor.execute("DELETE FROM daily_prices WHERE trade_date >= '20260601'")
    for i in range(5):
        date_str = (datetime(2026, 6, 13) - timedelta(days=i)).strftime("%Y%m%d")
        # 每天写入 10 条股票价格，上涨 6 条，下跌 4 条，总成交额 10 亿 (1000000000)
        for j in range(10):
            ts_code = f"{j:06d}.SH"
            pct_chg = 1.5 if j < 6 else -1.0
            amount = 100000000.0
            cursor.execute(
                "INSERT INTO daily_prices (ts_code, trade_date, open, high, low, close, pct_chg, vol, amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts_code, date_str, 10.0, 10.5, 9.8, 10.2, pct_chg, 10000, amount)
            )

    # 2. 注入 hsgt_moneyflow 数据 (流入 1 亿)
    cursor.execute("DELETE FROM hsgt_moneyflow WHERE trade_date >= '20260601'")
    cursor.execute("INSERT INTO hsgt_moneyflow (trade_date, north_money) VALUES ('20260613', 100000000.0)")

    # 3. 注入 daily_market_post 数据 (跌停 = 5，最高连板 = 5)
    cursor.execute("DELETE FROM daily_market_post WHERE trade_date >= '20260601'")
    cursor.execute(
        "INSERT INTO daily_market_post (trade_date, limit_up, limit_down, max_board, board_rate, continue_rate, total_amount) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ('20260613', 20, 5, 5, 0.8, 0.3, 1000000000.0)
    )

    # 4. 注入 global_macro_daily 数据 (VIX = 15.0, 美股均涨 0.5%)
    cursor.execute("DELETE FROM global_macro_daily WHERE trade_date >= '20260601'")
    cursor.execute(
        "INSERT INTO global_macro_daily (trade_date, vix, brent_price, brent_pct, dxy, usdcnh, dji_pct, ixic_pct, spx_pct, kospi_pct, n225_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ('20260613', 15.0, 80.0, 0.0, 100.0, 6.5, 0.5, 0.5, 0.5, 0.5, 0.5)
    )

    # 5. 注入 daily_index 25 天数据 (让 close > ma20 成立，从而均线良性)
    cursor.execute("DELETE FROM daily_index WHERE trade_date >= '20260501'")
    # 上证指数 000001.SH
    for i in range(25):
        date_str = (datetime(2026, 6, 13) - timedelta(days=i)).strftime("%Y%m%d")
        close_val = 3000.0 - i # 往前的价格越来越低，最新一天 (3000) 必然大于均线值
        cursor.execute(
            "INSERT INTO daily_index (ts_code, trade_date, open, high, low, close, pct_chg, vol, amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ('000001.SH', date_str, 3000.0, 3010.0, 2990.0, close_val, 0.1, 10000, 1000000.0)
        )
    # 深证成指 399001.SZ
    for i in range(25):
        date_str = (datetime(2026, 6, 13) - timedelta(days=i)).strftime("%Y%m%d")
        close_val = 10000.0 - i
        cursor.execute(
            "INSERT INTO daily_index (ts_code, trade_date, open, high, low, close, pct_chg, vol, amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ('399001.SZ', date_str, 10000.0, 10050.0, 9950.0, close_val, 0.1, 10000, 1000000.0)
        )

    conn.commit()
    conn.close()
    print("✅ [Setup] 测试数据注入完成。")


def teardown_test_data():
    """测试完成后，清理注入的临时测试数据"""
    print("🧹 [Teardown] 正在清理临时注入的测试数据...")
    conn = sqlite3.connect('db/stock_daily.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM daily_prices WHERE trade_date >= '20260601'")
    cursor.execute("DELETE FROM hsgt_moneyflow WHERE trade_date >= '20260601'")
    cursor.execute("DELETE FROM daily_market_post WHERE trade_date >= '20260601'")
    cursor.execute("DELETE FROM global_macro_daily WHERE trade_date >= '20260601'")
    cursor.execute("DELETE FROM daily_index WHERE trade_date >= '20260501'")
    conn.commit()
    conn.close()
    print("✅ [Teardown] 测试数据清理完成。")


def main():
    print("===== 第三阶段：一票否决 + 环境健康度 测试 =====")
    
    # 1. 运行空数据下的“存疑即止”降级否决测试
    print("\n--- 场景 A: 数据库空置，验证 [存疑即止] 一票否决机制 ---")
    res_a = macro_veto.run()
    print(f"  是否触发否决: {res_a['veto_result']['veto_triggered']}")
    print(f"  否决触发原因: {res_a['veto_result']['trigger_reason']}")
    print(f"  流程流向状态: {res_a['veto_result']['flow_status']}")
    
    # 2. 注入正常平稳数据，验证“未触发”和“健康度良性”路径
    print("\n--- 场景 B: 注入健康平稳数据，验证规则全绿通路径 ---")
    setup_test_data()
    try:
        res_b = macro_veto.run()
        
        # 打印否决结果
        print("\n  【一票否决校验结果】")
        veto = res_b["veto_result"]
        print(f"  是否触发否决: {veto['veto_triggered']}")
        print(f"  否决类型: {veto['veto_type']}")
        print(f"  流程状态: {veto['flow_status']}")
        print(f"  触发原因: {veto['trigger_reason']}")
        if veto["missing_list"]:
            print(f"  缺失字段: {veto['missing_list']}")

        # 打印健康度结果
        print("\n  【市场环境健康度】")
        health = res_b["health_result"]
        print(f"  整体状态: {health['health_status']}")
        if health["risk_list"]:
            print(f"  风险项: {health['risk_list']}")
        if health["data_missing"]:
            print(f"  健康度缺失字段: {health['data_missing']}")
    finally:
        teardown_test_data()

    print("\n===== 测试结束，请查看日志与数据库 =====")


if __name__ == "__main__":
    main()

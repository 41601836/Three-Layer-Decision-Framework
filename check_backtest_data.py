import sqlite3
import os

DB_PATH = "db/stock_daily.db"

def check_data_coverage():
    """检查2025年6月-12月的日线数据完整性"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 统计每日数据覆盖情况
    cursor.execute("""
        SELECT trade_date, COUNT(*) as count
        FROM daily_prices
        WHERE trade_date BETWEEN '20250601' AND '20251231'
        GROUP BY trade_date
        ORDER BY trade_date
    """)
    
    results = cursor.fetchall()
    total_days = len(results)
    expected_days = 146  # 2025年6月-12月约146个交易日
    
    print("=== 2025年6月-12月日线数据检查 ===")
    print(f"实际交易日数: {total_days}")
    print(f"预期交易日数: {expected_days}")
    print(f"数据覆盖率: {total_days/expected_days:.1%}")
    
    if total_days < expected_days * 0.9:
        print("\n❌ 数据严重不足，请补拉数据！")
        return False
    
    # 检查股票数量
    cursor.execute("""
        SELECT COUNT(DISTINCT ts_code)
        FROM daily_prices
        WHERE trade_date BETWEEN '20250601' AND '20251231'
    """)
    stock_count = cursor.fetchone()[0]
    print(f"\n覆盖股票数量: {stock_count}")
    
    # 检查是否有足够的数据进行回测
    cursor.execute("""
        SELECT ts_code, COUNT(*) as days
        FROM daily_prices
        WHERE trade_date BETWEEN '20250601' AND '20251231'
        GROUP BY ts_code
        HAVING COUNT(*) >= 100
    """)
    valid_stocks = cursor.fetchall()
    print(f"满足100天以上数据的股票数: {len(valid_stocks)}")
    
    conn.close()
    
    if len(valid_stocks) < 100:
        print("\n❌ 有效股票数量不足！")
        return False
    
    print("\n✅ 数据完整性检查通过！")
    return True

if __name__ == "__main__":
    check_data_coverage()
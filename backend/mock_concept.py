import sqlite3
import pandas as pd
from app.dao.base_dao import execute_update

def mock_concept_mapping(trade_date="20260617"):
    # 清理旧数据
    execute_update("DELETE FROM concept_mapping WHERE trade_date=?", (trade_date,))
    
    # 获取更多股票以便分配给 20 个板块 (取 2000 只)
    conn = sqlite3.connect("db/stock_daily.db")
    cursor = conn.cursor()
    cursor.execute(f"SELECT ts_code FROM daily_prices WHERE trade_date='{trade_date}' LIMIT 2000")
    codes = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    if not codes:
        print("daily_prices is empty for this date!")
        return

    # 20 个常见A股概念板块
    sectors = [
        "人工智能", "半导体", "新能源车", "低空经济", "医药生物", 
        "消费电子", "算力租赁", "通信设备", "汽车零部件", "电力设备", 
        "国防军工", "房地产开发", "证券", "银行", "白酒概念", 
        "中药", "文化传媒", "网络游戏", "光伏设备", "储能"
    ]
    
    for i, code in enumerate(codes):
        sector = sectors[i % len(sectors)]
        execute_update(
            "INSERT INTO concept_mapping (concept_name, ts_code, trade_date) VALUES (?, ?, ?)",
            (sector, code, trade_date)
        )
    
    print(f"Mocked {len(codes)} stocks into concept_mapping for {trade_date} across {len(sectors)} sectors.")

if __name__ == "__main__":
    mock_concept_mapping()
    
    from app.services.sector_aggregator import aggregate_sectors
    aggregate_sectors("20260617")

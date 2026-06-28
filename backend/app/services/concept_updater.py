import time
import logging
from app.core.tushare_client import get_pro, fetch_with_retry
from app.dao.base_dao import execute_update

logger = logging.getLogger(__name__)

def update_concept_mapping(trade_date: str):
    """
    抓取概念板块映射并更新入库 (使用 Tushare THS 概念接口)
    """
    logger.info(f"Updating concept mapping for trade_date: {trade_date} via Tushare THS")
    pro = get_pro()
    
    # 获取 THS 概念板块列表
    df_concept = fetch_with_retry(pro.ths_index, exchange='A', type='N', max_retries=3)
    if df_concept is None or df_concept.empty:
        logger.error("Failed to fetch THS concept list.")
        return
        
    # 先清理当天旧数据
    execute_update("DELETE FROM concept_mapping WHERE trade_date = ?", (trade_date,))
    
    # 为了演示速度，我们只取前 40 个活跃概念
    df_concept = df_concept.head(40)
        
    inserted = 0
    total = len(df_concept)
    
    logger.info(f"Found {total} concepts, starting component fetch...")
    
    for idx, row in df_concept.iterrows():
        ts_code = row['ts_code']
        concept_name = row['name']
        
        try:
            df_detail = fetch_with_retry(pro.ths_member, ts_code=ts_code, max_retries=3)
        except Exception as e:
            logger.debug(f"Failed to fetch components for {concept_name}: {e}")
            time.sleep(0.5)
            continue
            
        if df_detail is None or df_detail.empty:
            continue
            
        sql = """
            INSERT OR IGNORE INTO concept_mapping (concept_name, ts_code, trade_date)
            VALUES (?, ?, ?)
        """
        for _, detail_row in df_detail.iterrows():
            execute_update(sql, (concept_name, detail_row['con_code'], trade_date))
            inserted += 1
            
        # 控制调用频率
        time.sleep(0.3)
            
    logger.info(f"Concept mapping update complete. {inserted} rows processed.")


import time
import logging
from app.core.tushare_client import get_pro, fetch_with_retry
from app.dao.base_dao import execute_update

logger = logging.getLogger(__name__)

def update_concept_mapping(trade_date: str):
    """
    抓取概念板块映射并更新入库
    """
    logger.info(f"Updating concept mapping for trade_date: {trade_date}")
    pro = get_pro()
    
    # 获取所有概念板块列表
    df_concept = fetch_with_retry(pro.concept, max_retries=3)
    if df_concept is None or df_concept.empty:
        logger.error("Failed to fetch concept list from Tushare.")
        return
        
    inserted = 0
    total = len(df_concept)
    
    for _, row in df_concept.iterrows():
        concept_code = row['code']
        concept_name = row['name']
        
        # 获取该概念下的成分股
        df_detail = fetch_with_retry(pro.concept_detail, id=concept_code, max_retries=3)
        if df_detail is None or df_detail.empty:
            continue
            
        sql = """
            INSERT OR IGNORE INTO concept_mapping (concept_name, ts_code, trade_date)
            VALUES (?, ?, ?)
        """
        for _, detail_row in df_detail.iterrows():
            execute_update(sql, (concept_name, detail_row['ts_code'], trade_date))
            inserted += 1
            
        # 控制调用频率避免超限
        time.sleep(0.5)
        
    logger.info(f"Concept mapping update complete. {inserted} rows processed.")

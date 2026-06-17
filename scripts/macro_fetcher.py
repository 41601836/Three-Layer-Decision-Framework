# -*- coding: utf-8 -*-
"""
宏观数据自动化抓取器 (Phase 0)
利用 AkShare 提取国内/海外宏观数据，缓存入 sqlite 以供打分系统使用。
"""
import os
import sqlite3
import logging
from datetime import datetime
import akshare as ak

# ─── 路径配置 ─────────────────────────────────────────────────────────────────
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")
LOG_DIR  = os.path.join(ROOT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (macro) %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "macro_fetch.log"), encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("macro")

def get_db_conn():
    return sqlite3.connect(DB_PATH)

def _save_to_cache(indicator_code: str, value: float, trade_date: str):
    if value is None:
        log.warning(f"⚠️ 指标 {indicator_code} 获取到空值，跳过保存。")
        return
        
    conn = get_db_conn()
    try:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            INSERT OR REPLACE INTO macro_cache (indicator_code, indicator_value, trade_date, update_time, status)
            VALUES (?, ?, ?, ?, 'valid')
        """, (indicator_code, float(value), trade_date, now_str))
        conn.commit()
        log.info(f"✅ 成功更新指标: {indicator_code} = {value} (日期: {trade_date})")
    except Exception as e:
        log.error(f"❌ 数据库保存失败 [{indicator_code}]: {e}")
    finally:
        conn.close()

def fetch_overseas_macro():
    """
    抓取海外宏观指标：纳指、道指、标普、汇率、原油
    """
    today_str = datetime.now().strftime("%Y%m%d")
    log.info("🌐 开始抓取海外宏观指标...")
    
    # 1. 美股指数 (纳斯达克 ixic)
    try:
        df_nasdaq = ak.index_us_stock_sina(symbol="ixic")
        # index_us_stock_sina 返回最新的价格
        if not df_nasdaq.empty:
            close_val = df_nasdaq['close'].iloc[-1]
            _save_to_cache('US_NASDAQ', close_val, today_str)
    except Exception as e:
        log.error(f"纳斯达克获取失败: {e}")

    # 2. 汇率 (在岸人民币 USD/CNY)
    try:
        df_forex = ak.forex_spot_em()
        row = df_forex[df_forex['代码'] == 'USDCNY']
        if not row.empty:
            rate = row['最新价'].values[0]
            _save_to_cache('FX_USDCNY', rate, today_str)
    except Exception as e:
        log.error(f"汇率获取失败: {e}")
        
    # 3. 原油 (布伦特)
    try:
        df_oil = ak.futures_global_spot_em()
        row = df_oil[df_oil['名称'].str.contains('布伦特', na=False)]
        if not row.empty:
            price = row['最新价'].values[0]
            _save_to_cache('COMM_BRENT', price, today_str)
    except Exception as e:
        log.error(f"布伦特原油获取失败: {e}")

def fetch_domestic_macro():
    """
    抓取国内宏观数据：PMI, 社融 等
    """
    today_str = datetime.now().strftime("%Y%m%d")
    log.info("🇨🇳 开始抓取国内宏观数据...")
    
    # 1. PMI
    try:
        df_pmi = ak.macro_china_pmi()
        if not df_pmi.empty:
            latest = df_pmi.iloc[-1]
            pmi_val = latest['制造业-指数']
            # 取最新月份
            pmi_month = str(latest['月份']).replace('-', '')
            _save_to_cache('CN_PMI', pmi_val, pmi_month)
    except Exception as e:
        log.error(f"PMI 获取失败: {e}")

if __name__ == "__main__":
    log.info("=== 宏观抓取定时任务启动 ===")
    fetch_overseas_macro()
    fetch_domestic_macro()
    log.info("=== 宏观抓取完成 ===")

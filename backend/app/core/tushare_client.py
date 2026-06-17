import os
import time
import json
import logging
import tushare as ts

logger = logging.getLogger(__name__)

# Try to get config.json from project root
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")

_pro = None

def get_pro():
    global _pro
    if _pro is not None:
        return _pro
    
    token = os.environ.get("TUSHARE_TOKEN")
    if not token and os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                token = config.get("api", {}).get("tushare_token")
        except Exception as e:
            logger.error(f"Failed to read config.json: {e}")
            
    if not token:
        logger.warning("No TUSHARE_TOKEN found. Tushare API may be restricted.")
    else:
        ts.set_token(token)
        
    _pro = ts.pro_api()
    return _pro

def fetch_with_retry(func, max_retries=3, retry_delay=1.0, **kwargs):
    """
    Execute a Tushare API call with retries on failure (like rate limits or timeouts).
    """
    attempt = 0
    while attempt < max_retries:
        try:
            return func(**kwargs)
        except Exception as e:
            attempt += 1
            logger.warning(f"Tushare API error on attempt {attempt}/{max_retries}: {e}")
            if attempt >= max_retries:
                logger.error(f"Max retries reached. Tushare API call failed.")
                return None
            time.sleep(retry_delay)
    return None

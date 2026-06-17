import logging
import akshare as ak

logger = logging.getLogger(__name__)

def fetch_overseas_macro() -> dict:
    """
    获取海外宏观数据
    Returns: {'spx': float, 'vix': float, 'usdcny': float, 'brent': float}
    """
    result = {'spx': None, 'vix': None, 'usdcny': None, 'brent': None}
    
    # 1. 标普500 (SPX)
    try:
        # stock_us_spot_em() is more stable than stock_us_quote in recent versions
        df_spx = ak.stock_us_spot_em()
        # Find S&P 500 (usually symbol starting with .INX or similar, but let's try DJI or S&P)
        # To be safe and compliant, we try to use stock_us_spot_em or similar
        spx_row = df_spx[df_spx['名称'] == '标普500']
        if not spx_row.empty:
            result['spx'] = float(spx_row['最新价'].iloc[0])
    except Exception as e:
        logger.warning(f"Failed to fetch SPX: {e}")

    # 2. VIX
    try:
        if hasattr(ak, 'futures_global_hist'):
            df_vix = ak.futures_global_hist(symbol="VIX")
            result['vix'] = float(df_vix['收盘'].iloc[-1])
        else:
            # Fallback
            df_vix = ak.index_us_stock_sina(symbol="VIX")
            result['vix'] = float(df_vix['close'].iloc[-1])
    except Exception as e:
        logger.warning(f"Failed to fetch VIX: {e}")

    # 3. USD/CNY
    try:
        if hasattr(ak, 'currency_boc'):
            df_fx = ak.currency_boc()
            # Usually USD is "美元"
            usd_row = df_fx[df_fx['货币名称'] == '美元']
            if not usd_row.empty:
                result['usdcny'] = float(usd_row['中行折算价'].iloc[0]) / 100.0
        else:
            df_fx = ak.forex_spot_em()
            usd_row = df_fx[df_fx['代码'] == 'USDCNY']
            if not usd_row.empty:
                result['usdcny'] = float(usd_row['最新价'].iloc[0])
    except Exception as e:
        logger.warning(f"Failed to fetch USD/CNY: {e}")

    # 4. Brent
    try:
        if hasattr(ak, 'futures_global_hist'):
            df_brent = ak.futures_global_hist(symbol="Brent")
            result['brent'] = float(df_brent['收盘'].iloc[-1])
        else:
            df_brent = ak.futures_global_spot_em()
            brent_row = df_brent[df_brent['名称'].str.contains('布伦特', na=False)]
            if not brent_row.empty:
                result['brent'] = float(brent_row['最新价'].iloc[0])
    except Exception as e:
        logger.warning(f"Failed to fetch Brent: {e}")

    return result

def fetch_domestic_macro() -> dict:
    """
    获取国内宏观数据
    Returns: {'pmi': float, 'cpi': float, 'ppi': float, 'social_finance': float}
    """
    result = {'pmi': None, 'cpi': None, 'ppi': None, 'social_finance': None}

    # 1. PMI
    try:
        df_pmi = ak.macro_china_pmi()
        result['pmi'] = float(df_pmi['制造业-指数'].iloc[-1])
    except Exception as e:
        logger.warning(f"Failed to fetch PMI: {e}")

    # 2. CPI
    try:
        df_cpi = ak.macro_china_cpi_monthly()
        result['cpi'] = float(df_cpi['全国-当月同比'].iloc[-1])
    except Exception as e:
        logger.warning(f"Failed to fetch CPI: {e}")

    # 3. PPI
    try:
        df_ppi = ak.macro_china_ppi_yearly()
        # the exact column name varies, try common ones
        col = [c for c in df_ppi.columns if '同比' in c]
        if col:
            result['ppi'] = float(df_ppi[col[0]].iloc[-1])
    except Exception as e:
        logger.warning(f"Failed to fetch PPI: {e}")

    # 4. Social Finance (社融)
    try:
        df_sf = ak.macro_china_shrzgm()
        result['social_finance'] = float(df_sf['社会融资规模增量'].iloc[-1])
    except Exception as e:
        logger.warning(f"Failed to fetch Social Finance: {e}")

    return result

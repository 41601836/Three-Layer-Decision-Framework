# monday_warfare/strategy.py
import logging
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

logger = logging.getLogger("monday_wave")

from .db_setup import DB_PATH

def macd(close, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, min_periods=slow).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, min_periods=signal).mean()
    macd_hist = 2 * (dif - dea)
    return dif, dea, macd_hist

def get_market_sentiment(conn, last_date):
    try:
        limit_df = pd.read_sql(f"SELECT * FROM limit_data WHERE trade_date='{last_date}'", conn)
        if limit_df.empty:
            return None
        up_count = len(limit_df[limit_df['limit_type']=='U'])
        broken_count = len(limit_df[limit_df['limit_type']=='Z'])
        total = up_count + broken_count
        boom_rate = up_count / total if total > 0 else 0
        broken_rate = broken_count / total if total > 0 else 0
        return {'up_count':up_count, 'broken_rate':broken_rate, 'boom_rate':boom_rate}
    except Exception:
        return None

def run_strategy(risk_profile='稳健', num_stocks=3, account_size=150000):
    conn = sqlite3.connect(DB_PATH)
    
    try:
        # 1. 环境判断
        last_date = pd.read_sql("SELECT MAX(trade_date) FROM daily_prices", conn).iloc[0,0]
        if not last_date:
            return {'status':'skip', 'message':'数据库中没有日线数据'}
        logger.info(f"策略运行基准日期: {last_date}")
        
        # 尝试获取上证指数数据，如果没有则使用默认值
        index_df = pd.read_sql(f"SELECT * FROM daily_prices WHERE ts_code='000001.SH' ORDER BY trade_date", conn)
        if len(index_df) >= 60:
            index_df['ma60'] = index_df['close'].rolling(60).mean()
            latest = index_df.iloc[-1]
            close_idx = latest['close']
            ma60_idx = latest['ma60']
        else:
            close_idx = 3500
            ma60_idx = 3400
            logger.warning("上证指数数据不足，使用默认值进行环境判断")
        
        amount_total = pd.read_sql(f"SELECT SUM(amount) as total_amount FROM daily_prices WHERE trade_date='{last_date}'", conn).iloc[0,0] / 1e8
        margin_df = pd.read_sql(f"SELECT * FROM margin_summary ORDER BY trade_date DESC LIMIT 5", conn)
        if len(margin_df) >= 3:
            margin_change = margin_df['rzye'].diff().iloc[-3:].sum()
        else:
            margin_change = 0
        
        sentiment = get_market_sentiment(conn, last_date)
        boom_rate = sentiment['boom_rate'] if sentiment else 0.3
    
        if close_idx < ma60_idx * 0.97 or amount_total < 7000:
            conn.close()
            return {'status':'skip', 'message':'市场环境暴跌或成交低迷，本周暂停操作'}
        elif amount_total >= 10000 and boom_rate > 0.5:
            env_rating = '良好'
            position_limit = 0.8
        else:
            env_rating = '中性'
            position_limit = 0.6
        logger.info(f"市场环境评级: {env_rating}, 仓位上限: {position_limit*100}%")
    
        # 2. 主线板块
        three_dates = pd.read_sql(f"SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date DESC LIMIT 3", conn)['trade_date'].tolist()
        ind_map = pd.read_sql("SELECT ts_code, industry FROM stock_list", conn, index_col='ts_code')
        
        # 【修改】资金流表为空时跳过板块资金排序，避免报错
        try:
            mf = pd.read_sql(f"SELECT ts_code, trade_date, net_mf_amount FROM moneyflow_daily WHERE trade_date IN {tuple(three_dates)}", conn)
            if mf.empty:
                raise ValueError("资金流数据为空")
            mf['industry'] = mf['ts_code'].map(ind_map['industry'])
            mf_sum = mf.groupby('industry')['net_mf_amount'].sum().reset_index()
        except Exception as e:
            logger.warning(f"资金流数据异常，跳过板块资金排序: {str(e)}")
            mf_sum = pd.DataFrame({'industry': ind_map['industry'].unique(), 'net_mf_amount': 0})
        
        price = pd.read_sql(f"SELECT ts_code, trade_date, pct_chg FROM daily_prices WHERE trade_date IN {tuple(three_dates)}", conn)
        price['industry'] = price['ts_code'].map(ind_map['industry'])
        price_mean = price.groupby('industry')['pct_chg'].mean().reset_index()
        
        combined = pd.merge(price_mean, mf_sum, on='industry')
        combined.sort_values('pct_chg', ascending=False, inplace=True)
        top_sectors = combined.head(5)['industry'].tolist()
        logger.info(f"主线板块TOP5: {top_sectors}")
        
        # 3. 初筛排除
        candidates = pd.read_sql("SELECT * FROM stock_list WHERE ts_code NOT LIKE '%ST%'", conn)
        logger.info(f"初始股票池（去ST）: {len(candidates)} 只")
        
        # 涨停排除
        try:
            limit_today = pd.read_sql(f"SELECT ts_code FROM limit_data WHERE trade_date='{last_date}' AND limit_type='U'", conn)
            candidates = candidates[~candidates['ts_code'].isin(limit_today['ts_code'])]
            logger.info(f"过滤当日涨停股后剩余: {len(candidates)} 只")
        except Exception as e:
            logger.warning(f"涨跌停数据异常，跳过: {str(e)}")
        
        # 解禁排除
        try:
            unlock = pd.read_sql("SELECT DISTINCT ts_code FROM share_unlock", conn)
            candidates = candidates[~candidates['ts_code'].isin(unlock['ts_code'])]
            logger.info(f"过滤解禁股后剩余: {len(candidates)} 只")
        except Exception as e:
            logger.warning(f"解禁数据异常，跳过: {str(e)}")
        
        # 商誉排除
        try:
            bs = pd.read_sql("SELECT ts_code, goodwill, total_equity FROM balancesheet", conn)
            bs['ratio'] = bs['goodwill'] / bs['total_equity']
            high_goodwill = bs[bs['ratio']>0.3]['ts_code'].unique()
            candidates = candidates[~candidates['ts_code'].isin(high_goodwill)]
            logger.info(f"过滤高商誉后剩余: {len(candidates)} 只")
        except Exception as e:
            logger.warning(f"资产负债表数据异常，跳过: {str(e)}")
        
        # 质押排除
        try:
            pledge = pd.read_sql("SELECT ts_code, MAX(pledge_ratio) as max_pledge FROM pledge_stat GROUP BY ts_code", conn)
            high_pledge = pledge[pledge['max_pledge']>70]['ts_code'].unique()
            candidates = candidates[~candidates['ts_code'].isin(high_pledge)]
            logger.info(f"过滤高质押后剩余: {len(candidates)} 只")
        except Exception as e:
            logger.warning(f"质押数据异常，跳过: {str(e)}")
        
        # 业绩暴雷排除
        try:
            fc = pd.read_sql("SELECT ts_code FROM forecast WHERE type LIKE '%亏%' OR (type LIKE '%减%' AND p_change_min < -50)", conn)
            candidates = candidates[~candidates['ts_code'].isin(fc['ts_code'])]
            logger.info(f"过滤业绩暴雷后剩余: {len(candidates)} 只")
        except Exception as e:
            logger.warning(f"业绩预告数据异常，跳过: {str(e)}")
        
        # 4. 核心技术筛选
        final = []
        # 预加载所有股票的日线数据（正序），避免循环查询数据库，修复倒序bug
        all_daily = pd.read_sql(f"""
            SELECT ts_code, trade_date, close, open, high, low, vol, amount 
            FROM daily_prices 
            WHERE trade_date >= date('{last_date}', '-90 day')
            ORDER BY ts_code, trade_date
        """, conn)
        daily_grouped = all_daily.groupby('ts_code')
        
        # 预加载周线数据
        all_weekly = pd.read_sql(f"""
            SELECT ts_code, trade_date, close 
            FROM weekly_prices 
            WHERE trade_date >= date('{last_date}', '-200 day')
            ORDER BY ts_code, trade_date
        """, conn)
        weekly_grouped = all_weekly.groupby('ts_code')
        
        # 预加载资金流数据
        all_mf = pd.read_sql(f"""
            SELECT ts_code, trade_date, net_mf_amount 
            FROM moneyflow_daily 
            WHERE trade_date >= date('{last_date}', '-10 day')
            ORDER BY ts_code, trade_date DESC
        """, conn)
        mf_grouped = all_mf.groupby('ts_code')

        logger.info("开始核心技术指标筛选...")
        for _, row in candidates.iterrows():
            code = row['ts_code']
            
            # --- 小账户价格过滤 ---
            if code not in daily_grouped.groups:
                continue
            stock_daily = daily_grouped.get_group(code)
            if len(stock_daily) < 20:
                continue
            close_price = stock_daily.iloc[-1]['close']
            if close_price * 100 > account_size * 0.15:
                continue
            
            # --- 日线MA20（修复bug：数据已正序，计算正确） ---
            stock_daily['ma20'] = stock_daily['close'].rolling(20).mean()
            latest_ma20 = stock_daily.iloc[-1]['ma20']
            # 股价站稳MA20
            if close_price < latest_ma20:
                continue
            # MA20趋势：近3天走平或向上（可按需注释掉放宽）
            if len(stock_daily) >= 23:
                ma20_trend = stock_daily['ma20'].iloc[-3:].diff().mean()
                if ma20_trend < 0:
                    continue
            
            # --- 周线MACD（放宽条件：绿柱缩短/即将金叉/已金叉都保留） ---
            if code not in weekly_grouped.groups:
                continue
            stock_weekly = weekly_grouped.get_group(code)
            if len(stock_weekly) < 26:
                continue
            dif, dea, hist = macd(stock_weekly['close'])
            latest_dif = dif.iloc[-1]
            latest_dea = dea.iloc[-1]
            
            # 绿柱连续3根缩短
            if len(hist) >= 3:
                hist_shorten = hist.iloc[-1] > hist.iloc[-2] > hist.iloc[-3]
            else:
                hist_shorten = False
            # 接近金叉（差值收窄到5%以内）
            near_golden = (latest_dif - latest_dea) > -0.05 * abs(latest_dea)
            # 已金叉且红柱
            already_golden = latest_dif > latest_dea and hist.iloc[-1] > 0
            
            if not (hist_shorten or near_golden or already_golden):
                continue
            
            # --- 5日主力净流入（放宽条件：累计净流入>0 且 至少2天净流入） ---
            if code in mf_grouped.groups:
                stock_mf = mf_grouped.get_group(code).head(5)
                if len(stock_mf) >= 3:
                    net_inflow_sum = stock_mf['net_mf_amount'].sum()
                    inflow_days = (stock_mf['net_mf_amount'] > 0).sum()
                    if net_inflow_sum <= 0 or inflow_days < 2:
                        continue
            
            # --- 量比（放宽区间：0.7 ~ 2.0） ---
            if len(stock_daily) >= 6:
                vol_5_mean = stock_daily['vol'].iloc[-6:-1].mean()  # 前5日平均（不含当日）
                volume_ratio = stock_daily['vol'].iloc[-1] / vol_5_mean if vol_5_mean > 0 else 0
            else:
                volume_ratio = 1
            if not (0.7 <= volume_ratio <= 2.0):
                continue
            
            # --- 筹码空间（放宽阈值：3% → 1.5%） ---
            if len(stock_daily) >= 60:
                high_60 = stock_daily['high'].iloc[-60:].max()
                space = (high_60 - close_price) / close_price
            else:
                space = 0.05  # 数据不足默认有空间
            if space < 0.015:
                continue
            
            final.append(code)
        
        logger.info(f"核心技术筛选后剩余: {len(final)} 只")
        
        # 5. 排序打分
        if len(final) == 0:
            conn.close()
            return {'status':'skip', 'message':'没有符合所有条件的股票，请放宽筛选参数'}
        
        final_df = pd.DataFrame({'ts_code': final})
        final_df['industry'] = final_df['ts_code'].map(ind_map['industry'])
        final_df['in_main'] = final_df['industry'].isin(top_sectors).astype(int)
        final_df = final_df.merge(mf_sum, on='industry', how='left')
        final_df.sort_values(['in_main','net_mf_amount'], ascending=False, inplace=True)
        selected = final_df.head(num_stocks)
        
        picks = []
        for _, s in selected.iterrows():
            try:
                name = pd.read_sql(f"SELECT name FROM stock_list WHERE ts_code='{s['ts_code']}'", conn).iloc[0,0]
                close_price = pd.read_sql(f"SELECT close FROM daily_prices WHERE ts_code='{s['ts_code']}' AND trade_date='{last_date}'", conn).iloc[0,0]
                picks.append({
                    'ts_code': s['ts_code'],
                    'name': name,
                    'close': round(close_price, 2),
                    'industry': s['industry']
                })
            except Exception:
                continue
        
        conn.close()
        logger.info(f"策略执行完成，最终选出 {len(picks)} 只股票")
        return {
            'status': 'ok',
            'env_rating': env_rating,
            'position_limit': position_limit,
            'sentiment': sentiment,
            'top_sectors': top_sectors,
            'picks': picks,
            'last_date': last_date,
            'candidate_count': len(final)
        }
    except Exception as e:
        conn.close()
        logger.error(f"策略执行出错: {str(e)}", exc_info=True)
        return {'status':'skip', 'message':f'策略执行出错: {str(e)}'}
# -*- coding: utf-8 -*-
"""
macro_query.py —— 第一层宏观环境诊断专用数据查询模块
===================================================

基于 db.dao 数据库访问层封装，为三层决策框架中的“第一层 宏观环境诊断”提供一键化、高容错、
带缺失标注的指标获取接口，解耦业务逻辑与底层 SQL 细节。
"""

import os
import sqlite3
import pandas as pd
from typing import Dict, List, Any, Optional

from db.dao import dao
from utils.logger import collect_log
from decision_framework.decision_log import decision_log
from config_loader import *


class MacroQuery:
    """
    第一层宏观指标专用查询工具类。
    所有查询动作、缺失状态均输出 decision_log。
    若数据表/字段缺失或数据库异常，会自动优雅降级，并返回 data_missing=True 的数据结构。
    """

    def _execute_query_single(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        """
        内部辅助方法：执行 SQL 并返回单行数据字典。
        """
        conn = dao.get_conn()
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(sql, params)
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        except Exception as e:
            decision_log.error(f"❌ [MacroQuery] 执行 SQL 异常: {e} | SQL: {sql} | 参数: {params}")
            return None
        finally:
            conn.close()

    def _execute_query_list(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """
        内部辅助方法：执行 SQL 并返回多行数据字典列表。
        """
        conn = dao.get_conn()
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            decision_log.error(f"❌ [MacroQuery] 执行 SQL 异常: {e} | SQL: {sql} | 参数: {params}")
            return []
        finally:
            conn.close()

    def _get_max_date(self, table_name: str, date_col: str = "trade_date", filter_sql: str = "", filter_params: tuple = ()) -> Optional[str]:
        """
        内部辅助方法：获取指定表中的最新交易日期。
        """
        sql = f"SELECT MAX({date_col}) FROM {table_name}"
        if filter_sql:
            sql += f" WHERE {filter_sql}"
        
        conn = dao.get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(sql, filter_params)
            row = cursor.fetchone()
            if row and row[0]:
                return str(row[0])
            return None
        except Exception as e:
            decision_log.debug(f"[MacroQuery] 获取表 {table_name} 的最新日期异常: {e}")
            return None
        finally:
            conn.close()

    # =========================================================================
    # 核心查询方法实现
    # =========================================================================

    def get_global_macro(self, trade_date: str = None) -> Dict[str, Any]:
        """
        1. 获取全球宏观（美股、VIX、原油、美元/人民币、日韩指数）指标。
        """
        decision_log.info(f"📊 [MacroQuery] 正在查询全球宏观指标... 指定日期: {trade_date or '最新'}")
        
        # 1. 确定日期
        if not trade_date:
            trade_date = self._get_max_date("global_macro_daily")
        
        if not trade_date:
            decision_log.warning("⚠️ [MacroQuery] 全球宏观表 global_macro_daily 无数据，启用数据缺失降级。")
            return {"trade_date": None, "vix": None, "brent_price": None, "brent_pct": None, "dxy": None, 
                    "usdcnh": None, "dji_pct": None, "ixic_pct": None, "spx_pct": None, "kospi_pct": None, 
                    "n225_pct": None, "data_missing": True}
        
        # 2. 查询数据
        sql = "SELECT * FROM global_macro_daily WHERE trade_date = ?"
        res = self._execute_query_single(sql, (trade_date,))
        
        if not res:
            decision_log.warning(f"⚠️ [MacroQuery] 未能查询到全球宏观数据 -> 日期: {trade_date}")
            return {"trade_date": trade_date, "vix": None, "brent_price": None, "brent_pct": None, "dxy": None, 
                    "usdcnh": None, "dji_pct": None, "ixic_pct": None, "spx_pct": None, "kospi_pct": None, 
                    "n225_pct": None, "data_missing": True}
        
        res["data_missing"] = False
        decision_log.info(f"✅ [MacroQuery] 全球宏观查询成功 -> 日期: {trade_date}")
        return res

    def get_market_capital(self, trade_date: str = None) -> Dict[str, Any]:
        """
        2. 获取市场资金（两市成交额、北向资金、两融资金、ETF）。
        """
        decision_log.info(f"📊 [MacroQuery] 正在查询市场资金指标... 指定日期: {trade_date or '最新'}")
        
        # 1. 获取成交额最新交易日并求和
        prices_date = trade_date
        if not prices_date:
            prices_date = self._get_max_date("daily_prices")
        
        total_amount = None
        if prices_date:
            sql_amt = "SELECT SUM(amount) FROM daily_prices WHERE trade_date = ?"
            res_amt = self._execute_query_single(sql_amt, (prices_date,))
            if res_amt and res_amt.get("SUM(amount)") is not None:
                total_amount = float(res_amt["SUM(amount)"])
        
        # 2. 获取北向资金流入
        hsgt_date = trade_date
        if not hsgt_date:
            hsgt_date = self._get_max_date("hsgt_moneyflow")
            
        north_money = None
        if hsgt_date:
            sql_ns = "SELECT north_money FROM hsgt_moneyflow WHERE trade_date = ?"
            res_ns = self._execute_query_single(sql_ns, (hsgt_date,))
            if res_ns and res_ns.get("north_money") is not None:
                north_money = float(res_ns["north_money"])
                
        # 3. 获取两融余额
        margin_date = trade_date
        if not margin_date:
            margin_date = self._get_max_date("margin_detail")
            
        margin_balance = None
        if margin_date:
            sql_mg = "SELECT SUM(rzye) FROM margin_detail WHERE trade_date = ?"
            res_mg = self._execute_query_single(sql_mg, (margin_date,))
            if res_mg and res_mg.get("SUM(rzye)") is not None:
                margin_balance = float(res_mg["SUM(rzye)"])
                
        # 4. ETF流向暂无独立表，降级处理为 None
        etf_flow = None
        
        # 5. 校验缺失性 (核心成交额不存在则判定为缺失)
        data_missing = (total_amount is None)
        
        ret = {
            "trade_date": prices_date or trade_date,
            "total_amount": total_amount,
            "north_money": north_money,
            "margin_balance": margin_balance,
            "etf_flow": etf_flow,
            "data_missing": data_missing
        }
        
        if data_missing:
            decision_log.warning(f"⚠️ [MacroQuery] 市场资金数据关键指标缺失 -> 结果: {ret}")
        else:
            decision_log.info(f"✅ [MacroQuery] 市场资金查询成功 -> 日期: {prices_date}, 成交额: {total_amount:.2f}")
        return ret

    def get_market_sentiment(self, trade_date: str = None) -> Dict[str, Any]:
        """
        3. 获取市场情绪（涨跌家数、涨跌停、连板、封板、情绪天数）。
        """
        decision_log.info(f"📊 [MacroQuery] 正在查询市场情绪指标... 指定日期: {trade_date or '最新'}")
        
        # 1. 确定盘后表日期
        post_date = trade_date
        if not post_date:
            post_date = self._get_max_date("daily_market_post")
            
        limit_up = None
        limit_down = None
        board_rate = None
        continue_rate = None
        
        if post_date:
            sql_post = "SELECT * FROM daily_market_post WHERE trade_date = ?"
            res_post = self._execute_query_single(sql_post, (post_date,))
            if res_post:
                limit_up = res_post.get("limit_up")
                limit_down = res_post.get("limit_down")
                board_rate = res_post.get("board_rate")
                continue_rate = res_post.get("continue_rate")
                
        # 2. 查询每日个股的涨跌家数
        prices_date = post_date or trade_date
        if not prices_date:
            prices_date = self._get_max_date("daily_prices")
            
        up_count = None
        down_count = None
        if prices_date:
            sql_cnt = """
                SELECT 
                    COUNT(*) as total, 
                    SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END) as up_num,
                    SUM(CASE WHEN pct_chg < 0 THEN 1 ELSE 0 END) as down_num
                FROM daily_prices 
                WHERE trade_date = ?
            """
            res_cnt = self._execute_query_single(sql_cnt, (prices_date,))
            if res_cnt and res_cnt.get("total", 0) > 0:
                up_count = res_cnt.get("up_num")
                down_count = res_cnt.get("down_num")
                
        # 3. 校验缺失性 (涨停/跌停或涨跌家数任一不存在则标记为缺失)
        data_missing = (limit_up is None or limit_down is None or up_count is None)
        
        ret = {
            "trade_date": prices_date or post_date or trade_date,
            "up_count": up_count,
            "down_count": down_count,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "board_rate": board_rate,
            "continue_rate": continue_rate,
            "data_missing": data_missing
        }
        
        if data_missing:
            decision_log.warning(f"⚠️ [MacroQuery] 市场情绪数据部分指标缺失 -> 结果: {ret}")
        else:
            decision_log.info(f"✅ [MacroQuery] 市场情绪查询成功 -> 日期: {prices_date}, 涨跌停: {limit_up}/{limit_down}")
        return ret

    def get_macro_econ(self, trade_date: str = None) -> Dict[str, Any]:
        """
        4. 获取国内宏观经济数据（PMI/CPI/PPI/社融/GDP）。
        """
        decision_log.info(f"📊 [MacroQuery] 正在查询国内经济宏观数据... 指定日期: {trade_date or '最新'}")
        
        stat_month = None
        if trade_date:
            stat_month = trade_date[:6]
        else:
            stat_month = self._get_max_date("china_macro_indicators", date_col="stat_month")
            
        if not stat_month:
            decision_log.warning("⚠️ [MacroQuery] 国内经济宏观表 china_macro_indicators 无数据，启用数据缺失降级。")
            return {"stat_month": None, "pmi_man": None, "pmi_non": None, "cpi": None, "ppi": None, 
                    "gdp_growth": None, "social_fin": None, "data_missing": True}
        
        sql = "SELECT * FROM china_macro_indicators WHERE stat_month = ?"
        res = self._execute_query_single(sql, (stat_month,))
        
        if not res:
            decision_log.warning(f"⚠️ [MacroQuery] 未能查询到该月份的国内经济数据 -> 月份: {stat_month}")
            return {"stat_month": stat_month, "pmi_man": None, "pmi_non": None, "cpi": None, "ppi": None, 
                    "gdp_growth": None, "social_fin": None, "data_missing": True}
        
        res["data_missing"] = False
        decision_log.info(f"✅ [MacroQuery] 国内宏观经济查询成功 -> 月份: {stat_month}")
        return res

    def get_board_data(self, trade_date: str = None) -> List[Dict[str, Any]]:
        """
        5. 获取板块数据（全板块涨跌幅、资金、涨停覆盖率等）。
        """
        decision_log.info(f"📊 [MacroQuery] 正在查询全板块指标数据... 指定日期: {trade_date or '最新'}")
        
        if not trade_date:
            trade_date = self._get_max_date("board_money_flow")
            
        if not trade_date:
            decision_log.warning("⚠️ [MacroQuery] 板块表 board_money_flow 无数据，返回空列表。")
            return []
            
        sql = "SELECT * FROM board_money_flow WHERE trade_date = ?"
        res = self._execute_query_list(sql, (trade_date,))
        decision_log.info(f"✅ [MacroQuery] 板块数据查询成功 -> 日期: {trade_date}, 板块数: {len(res)}")
        return res

    def get_snapshot_1030(self, trade_date: str = None) -> Dict[str, Any]:
        """
        6. 获取 10:30 盘中半日快照。
        """
        decision_log.info(f"📊 [MacroQuery] 正在查询 10:30 盘中半日快照... 指定日期: {trade_date or '最新'}")
        
        if not trade_date:
            trade_date = self._get_max_date("market_snapshot", filter_sql="snapshot_time LIKE '%10:30'")
            
        if not trade_date:
            decision_log.warning("⚠️ [MacroQuery] 市场快照表 market_snapshot 无 10:30 快照数据，启用缺失降级。")
            return {"snapshot_time": None, "trade_date": None, "total_amount": None, "half_up_num": None, 
                    "half_down_num": None, "board_top_name": None, "board_top_change": None, "us_index_change": None, 
                    "kr_index_change": None, "jp_index_change": None, "data_missing": True}
        
        sql = "SELECT * FROM market_snapshot WHERE trade_date = ? AND snapshot_time LIKE '%10:30'"
        res = self._execute_query_single(sql, (trade_date,))
        
        if not res:
            decision_log.warning(f"⚠️ [MacroQuery] 未能查询到该日期的 10:30 快照数据 -> 日期: {trade_date}")
            return {"snapshot_time": None, "trade_date": trade_date, "total_amount": None, "half_up_num": None, 
                    "half_down_num": None, "board_top_name": None, "board_top_change": None, "us_index_change": None, 
                    "kr_index_change": None, "jp_index_change": None, "data_missing": True}
        
        res["data_missing"] = False
        decision_log.info(f"✅ [MacroQuery] 10:30 快照查询成功 -> 时间: {res.get('snapshot_time')}")
        return res

    def get_snapshot_1400(self, trade_date: str = None) -> Dict[str, Any]:
        """
        7. 获取 14:00 盘中快照。
        """
        decision_log.info(f"📊 [MacroQuery] 正在查询 14:00 盘中快照... 指定日期: {trade_date or '最新'}")
        
        if not trade_date:
            trade_date = self._get_max_date("market_snapshot", filter_sql="snapshot_time LIKE '%14:00'")
            
        if not trade_date:
            decision_log.warning("⚠️ [MacroQuery] 市场快照表 market_snapshot 无 14:00 快照数据，启用缺失降级。")
            return {"snapshot_time": None, "trade_date": None, "total_amount": None, "half_up_num": None, 
                    "half_down_num": None, "board_top_name": None, "board_top_change": None, "us_index_change": None, 
                    "kr_index_change": None, "jp_index_change": None, "data_missing": True}
        
        sql = "SELECT * FROM market_snapshot WHERE trade_date = ? AND snapshot_time LIKE '%14:00'"
        res = self._execute_query_single(sql, (trade_date,))
        
        if not res:
            decision_log.warning(f"⚠️ [MacroQuery] 未能查询到该日期的 14:00 快照数据 -> 日期: {trade_date}")
            return {"snapshot_time": None, "trade_date": trade_date, "total_amount": None, "half_up_num": None, 
                    "half_down_num": None, "board_top_name": None, "board_top_change": None, "us_index_change": None, 
                    "kr_index_change": None, "jp_index_change": None, "data_missing": True}
        
        res["data_missing"] = False
        decision_log.info(f"✅ [MacroQuery] 14:00 快照查询成功 -> 时间: {res.get('snapshot_time')}")
        return res

    def get_index_ma(self, ts_code: str = "000001.SH", trade_date: str = None) -> Dict[str, Any]:
        """
        8. 获取指定大盘指数（如上证指数 000001.SH / 万得全A 881001.WI）相对 20 日均线的位置。
        """
        decision_log.info(f"📊 [MacroQuery] 正在查询指数相对20日均线位置... 代码: {ts_code}, 日期: {trade_date or '最新'}")
        
        # 1. 确定日期
        if not trade_date:
            trade_date = self._get_max_date("daily_index", filter_sql="ts_code = ?", filter_params=(ts_code,))
            
        if not trade_date:
            decision_log.warning(f"⚠️ [MacroQuery] 指数表 daily_index 无该指数 [{ts_code}] 数据，启用缺失降级。")
            return {"ts_code": ts_code, "trade_date": None, "close": None, "ma20": None, 
                    "is_above_ma20": False, "data_missing": True}
        
        # 2. 查询包含指定日期在内，向前的最近 20 天收盘价列表
        sql = """
            SELECT trade_date, close 
            FROM daily_index 
            WHERE ts_code = ? AND trade_date <= ? 
            ORDER BY trade_date DESC 
            LIMIT 20
        """
        rows = self._execute_query_list(sql, (ts_code, trade_date))
        
        if len(rows) < 20:
            decision_log.warning(f"⚠️ [MacroQuery] 该日期往前的指数 [{ts_code}] 历史数据不足 20 天（当前 {len(rows)} 天），启用降级。")
            close_val = rows[0]["close"] if rows else None
            return {"ts_code": ts_code, "trade_date": trade_date, "close": close_val, "ma20": None, 
                    "is_above_ma20": False, "data_missing": True}
        
        # 3. 计算 20 日均线 (MA20)
        close_list = [float(r["close"]) for r in rows]
        ma20 = sum(close_list) / 20.0
        latest_close = close_list[0]
        is_above_ma20 = (latest_close > ma20)
        
        ret = {
            "ts_code": ts_code,
            "trade_date": trade_date,
            "close": latest_close,
            "ma20": ma20,
            "is_above_ma20": is_above_ma20,
            "data_missing": False
        }
        
        decision_log.info(f"✅ [MacroQuery] 指数 [{ts_code}] 20日均线查询成功 -> 收盘价: {latest_close:.2f}, MA20: {ma20:.2f}, 站上均线: {is_above_ma20}")
        return ret


# 对外提供全局单例查询对象
macro_query = MacroQuery()

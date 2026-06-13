# -*- coding: utf-8 -*-
"""
board_structure.py —— 第二层板块风格与轮动决策框架：板块梯队与中军结构识别子模块
========================================================================

本模块基于板块成份股的价格涨跌幅历史、市值及成交额，对板块内部微观结构进行量化剖析：
1. 梯队判定：计算连板梯队高度和断层阶梯。
2. 中军识别：通过流通市值（万元）与当日成交占比，识别大市值中军表现（强势/弱势/无中军）。
3. 级联评估：综合两者输出 [强结构 / 中性结构 / 弱结构]，辅助后继仓位调控。
"""

import sqlite3
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd
from db.dao import dao
from decision_framework.board_rank import board_rank
from config_loader import *
from decision_framework.decision_log import decision_log


class BoardStructure:
    """
    板块梯队与中军结构识别单例类。
    """

    def judge_ladder(
        self, board_name: str, stock_daily_df: pd.DataFrame, missing_list: List[str]
    ) -> Tuple[str, str]:
        """
        1. 板块梯队判定
        
        通过历史连板高度与断层统计对梯队进行评估。
        优秀：存在龙头（高度>=LEADER_MIN_BOARD）且断层数 < LADDER_BREAK_THRESHOLD
        一般：梯队断层超标，或未达龙头高度但有连板/涨停
        薄弱：板块无任何连板标的
        """
        try:
            if stock_daily_df.empty:
                missing_list.append(f"板块 [{board_name}] 梯队: 个股日线价格")
                return "数据不足", "个股日线历史数据缺失，无法判定梯队"

            # 1. 计算个股连续涨停天数 (连板天数)
            # 找到最新交易日
            latest_date = stock_daily_df["trade_date"].max()
            stocks = stock_daily_df["ts_code"].unique()
            individual_boards = {}

            for ts in stocks:
                df_stk = stock_daily_df[stock_daily_df["ts_code"] == ts].sort_values(by="trade_date", ascending=False)
                
                # 若最新交易日无记录或最新交易日没有涨停 (pct_chg < 9.5)，连板数为 0
                row_latest = df_stk[df_stk["trade_date"] == latest_date]
                if row_latest.empty:
                    individual_boards[ts] = 0
                    continue
                
                pct_latest = float(row_latest.iloc[0]["pct_chg"])
                if pct_latest < 9.5:
                    individual_boards[ts] = 0
                    continue

                # 循环前推计算连板
                boards = 0
                for _, row in df_stk.iterrows():
                    pct = float(row["pct_chg"])
                    if pct >= 9.5:
                        boards += 1
                    else:
                        break
                individual_boards[ts] = boards

            # 2. 板块龙头连板数统计
            max_board = max(individual_boards.values()) if individual_boards else 0
            
            # 3. 统计梯队与断层
            # 只有龙头高度大于 0 时才计算断层
            if max_board >= LEADER_MIN_BOARD:
                # 检查 1 ~ max_board-1 各高度是否有股票分布
                # 记录每一个阶梯的个股数量
                ladder_distribution = {i: 0 for i in range(1, max_board)}
                for val in individual_boards.values():
                    if 0 < val < max_board:
                        ladder_distribution[val] += 1
                
                # 统计没有股票的阶梯（断层）数量
                break_count = sum(1 for count in ladder_distribution.values() if count == 0)
                
                if break_count >= LADDER_BREAK_THRESHOLD:
                    rating = "一般"
                    reason = (
                        f"龙头高度达 {max_board} 板，但 1~{max_board-1} 板中"
                        f"存在 {break_count} 个断层阶梯 (阈值 {LADDER_BREAK_THRESHOLD})，梯队残缺断层。"
                    )
                else:
                    rating = "优秀"
                    reason = f"存在有效龙头 ({max_board}板)，且 1~{max_board-1} 连板梯队结构完整，无严重断层。"
            elif max_board > 0:
                rating = "一般"
                reason = f"板块最高连板仅 {max_board} 板，未达有效龙头门槛 ({LEADER_MIN_BOARD}板)。"
            else:
                # 检查是否有首板（最新一天有涨停）
                has_limit_up = any(v == 1 for v in individual_boards.values())
                if has_limit_up:
                    rating = "一般"
                    reason = "板块无连续连板标的，仅见零星首板涨停。"
                else:
                    rating = "薄弱"
                    reason = "板块内无任何涨停或连板标的，投机氛围薄弱。"

            decision_log.info(f"📊 [BoardStructure] 板块 [{board_name}] 连板龙头: {max_board}板 | 梯队评级: {rating} | 原因: {reason}")
            return rating, reason

        except Exception as e:
            decision_log.error(f"❌ [BoardStructure] 板块 [{board_name}] 判定梯队发生异常: {e}")
            return "薄弱", f"判定异常: {str(e)}，降级为薄弱"

    def judge_main_force(
        self, board_name: str, stock_basic_df: pd.DataFrame, stock_daily_df: pd.DataFrame, missing_list: List[str]
    ) -> Tuple[str, str, str]:
        """
        2. 中军标的识别
        
        流通盘 circ_mv * 10000 >= MAIN_FLOAT_THRESHOLD (50亿)
        个股成交额占板块总成交比 >= MAIN_TURNOVER_RATIO (8%)
        
        强势：核心中军日内收红盘 (pct_chg >= 0.0)
        弱势：核心中军日内下跌 (pct_chg < 0.0)
        返回: (中军名称, 中军评级, 判定原因)
        """
        try:
            if stock_basic_df.empty or stock_daily_df.empty:
                missing_list.append(f"板块 [{board_name}] 中军: 股票市值或价格")
                return "无", "数据不足", "个股流通盘或价格数据缺失，无法识别中军"

            # 筛选最新一日的数据
            latest_date = stock_daily_df["trade_date"].max()
            df_latest = stock_daily_df[stock_daily_df["trade_date"] == latest_date]

            if df_latest.empty:
                missing_list.append(f"板块 [{board_name}] 最新交易日价格")
                return "无", "数据不足", "最新一日交易价格数据缺失，无法识别中军"

            # 板块总成交额 (当日全部成分股成交额之和)
            total_board_amount = df_latest["amount"].sum()
            if total_board_amount <= 0:
                return "无", "无明确中军", "板块成交额为零，无法计算成交占比"

            # 合并个股日线与基本面流通市值 (circ_mv 在 basic_df 中，基本面 trade_date 需对齐最新交易日或最接近值)
            # 找到 basic_df 最新可用的日期
            basic_latest_date = stock_basic_df["trade_date"].max()
            df_basic_latest = stock_basic_df[stock_basic_df["trade_date"] == basic_latest_date]

            # 合并数据
            df_merged = pd.merge(df_latest, df_basic_latest, on="ts_code", suffixes=("_daily", "_basic"))

            # 筛选符合中军标准的个股
            # circ_mv 单位是万元，故 circ_mv * 10000 换算为“元”
            # 成交占比 = amount / total_board_amount
            df_merged["turnover_ratio"] = df_merged["amount"] / total_board_amount
            df_merged["circ_mv_yuan"] = df_merged["circ_mv"] * 10000.0

            df_sentry = df_merged[
                (df_merged["circ_mv_yuan"] >= MAIN_FLOAT_THRESHOLD) &
                (df_merged["turnover_ratio"] >= MAIN_TURNOVER_RATIO)
            ]

            if df_sentry.empty:
                # 降低门槛提示或返回无
                decision_log.info(f"ℹ️ [BoardStructure] 板块 [{board_name}] 未检索到满足流通盘({MAIN_FLOAT_THRESHOLD/1e8:.1f}亿)与成交比({MAIN_TURNOVER_RATIO:.1%})的核心中军")
                return "无", "无明确中军", "未发现满足大市值及高成交比要求的中军标的"

            # 如果有多只满足条件的股票，取流通市值 circ_mv 最大的那只作为“核心中军锚”
            core_sentry = df_sentry.sort_values(by="circ_mv_yuan", ascending=False).iloc[0]
            sentry_code = core_sentry["ts_code"]
            
            # 查找中军股名称
            sentry_name = sentry_code
            conn = dao.get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM stock_list WHERE ts_code = ?", (sentry_code,))
                row = cursor.fetchone()
                if row:
                    sentry_name = row[0]
            except Exception:
                pass
            finally:
                conn.close()

            pct_chg = float(core_sentry["pct_chg"])
            circ_mv_g = float(core_sentry["circ_mv_yuan"]) / 1e8
            ratio_g = float(core_sentry["turnover_ratio"])

            if pct_chg >= 0.0:
                rating = "强势"
                reason = (
                    f"识别中军 [{sentry_name}] (流通市值 {circ_mv_g:.2f}亿，"
                    f"成交占比 {ratio_g:.1%})，日内上涨 {pct_chg:+.2f}%，走势强势吸金。"
                )
            else:
                rating = "弱势"
                reason = (
                    f"识别中军 [{sentry_name}] (流通市值 {circ_mv_g:.2f}亿，"
                    f"成交占比 {ratio_g:.1%})，日内下跌 {pct_chg:+.2f}%，核心权重走弱。"
                )

            decision_log.info(f"📊 [BoardStructure] 板块 [{board_name}] 中军锚: {sentry_name} | 中军评级: {rating} | 原因: {reason}")
            return sentry_name, rating, reason

        except Exception as e:
            decision_log.error(f"❌ [BoardStructure] 板块 [{board_name}] 识别中军异常: {e}")
            return "无", "无明确中军", f"识别异常: {str(e)}，降级为无"

    def get_composite_rating(self, ladder_rating: str, main_rating: str) -> str:
        """
        3. 综合评级
        
        强结构：梯队优秀 + 中军强势
        弱结构：梯队薄弱 或 中军弱势
        中性结构：其他情况
        """
        if ladder_rating == "数据不足" or main_rating == "数据不足":
            return "数据不足"
        
        if ladder_rating == "优秀" and main_rating == "强势":
            return "强结构"
        elif ladder_rating == "薄弱" or main_rating == "弱势":
            return "弱结构"
        else:
            return "中性结构"

    def run(self, board_name: str) -> Dict[str, Any]:
        """
        板块内部微观结构评定统一入口。
        """
        decision_log.info(f"🚀 [BoardStructure] 开始评估板块 [{board_name}] 的内部结构与中军走势...")
        data_missing_list = []

        try:
            conn = dao.get_conn()
            cursor = conn.cursor()

            # 1. 查找板块对应的个股代码列表
            cursor.execute("SELECT ts_code FROM stock_list WHERE industry = ?", (board_name,))
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                data_missing_list.append(f"板块 [{board_name}] 成分股列表")
                decision_log.warning(f"⚠️ [BoardStructure] 数据库中未找到板块 [{board_name}] 的任何成份股记录")
                return {
                    "board_name": board_name,
                    "ladder_rating": "数据不足",
                    "ladder_reason": "数据库中无成分股关联记录",
                    "main_name": "无",
                    "main_rating": "数据不足",
                    "main_reason": "成份股缺失，无法判定中军",
                    "composite_rating": "数据不足",
                    "data_missing_list": data_missing_list
                }

            stock_codes = [r[0] for r in rows]

            # 2. 查询最近 20 天内这些个股的价格与成交额
            # 以获取连板历史和最新成交
            placeholders = ",".join(["?"] * len(stock_codes))
            
            # 先找价格表里最新可用的日期，以避免休市日数据缺乏
            conn = dao.get_conn()
            cursor = conn.cursor()
            cursor.execute(f"SELECT MAX(trade_date) FROM daily_prices WHERE ts_code IN ({placeholders})", stock_codes)
            row_date = cursor.fetchone()
            
            if not row_date or not row_date[0]:
                conn.close()
                data_missing_list.append(f"板块 [{board_name}] 价格数据(daily_prices)")
                return {
                    "board_name": board_name,
                    "ladder_rating": "数据不足",
                    "ladder_reason": "无日线价格记录",
                    "main_name": "无",
                    "main_rating": "数据不足",
                    "main_reason": "无个股价格记录，无法识别中军",
                    "composite_rating": "数据不足",
                    "data_missing_list": data_missing_list
                }
                
            latest_date = row_date[0]
            
            # 查询 20 天日线历史
            sql_prices = f"""
                SELECT ts_code, trade_date, pct_chg, amount, close 
                FROM daily_prices 
                WHERE ts_code IN ({placeholders}) AND trade_date <= ?
                ORDER BY trade_date DESC
            """
            # 查询最新的流通市值 (自由流通盘)
            sql_basic = f"""
                SELECT ts_code, trade_date, circ_mv, float_share 
                FROM daily_basic 
                WHERE ts_code IN ({placeholders})
                ORDER BY trade_date DESC
            """
            
            # 执行查询
            cursor.execute(sql_prices, stock_codes + [latest_date])
            price_rows = cursor.fetchall()
            
            cursor.execute(sql_basic, stock_codes)
            basic_rows = cursor.fetchall()
            
            conn.close()

            # 3. 转化为 DataFrame 处理
            price_df = pd.DataFrame(price_rows, columns=["ts_code", "trade_date", "pct_chg", "amount", "close"])
            basic_df = pd.DataFrame(basic_rows, columns=["ts_code", "trade_date", "circ_mv", "float_share"])

            # 限制每个股只取 20 条，防止内存开销
            price_df = price_df.groupby("ts_code").head(20).reset_index(drop=True)
            basic_df = basic_df.groupby("ts_code").head(1).reset_index(drop=True)  # 市值取最新 1 天即可

            # 4. 执行四大核心规则计算
            ladder_rating, ladder_reason = self.judge_ladder(board_name, price_df, data_missing_list)
            main_name, main_rating, main_reason = self.judge_main_force(board_name, basic_df, price_df, data_missing_list)
            composite_rating = self.get_composite_rating(ladder_rating, main_rating)

            return {
                "board_name": board_name,
                "ladder_rating": ladder_rating,
                "ladder_reason": ladder_reason,
                "main_name": main_name,
                "main_rating": main_rating,
                "main_reason": main_reason,
                "composite_rating": composite_rating,
                "data_missing_list": list(set(data_missing_list))
            }

        except Exception as e:
            decision_log.error(f"❌ [BoardStructure] 评估工作流突发异常: {e}，安全降级")
            return {
                "board_name": board_name,
                "ladder_rating": "薄弱",
                "ladder_reason": f"评估异常: {str(e)}",
                "main_name": "无",
                "main_rating": "弱势",
                "main_reason": f"评估异常: {str(e)}",
                "composite_rating": "弱结构",
                "data_missing_list": ["数据库或代码运行异常"]
            }


# 对外提供全局单例对象
board_structure = BoardStructure()

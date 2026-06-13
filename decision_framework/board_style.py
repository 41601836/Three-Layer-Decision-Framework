# -*- coding: utf-8 -*-
"""
board_style.py —— 第二层板块风格与轮动决策框架：市场风格划分与强度判定子模块
====================================================================

本模块承接第一层的板块打分结果，独立实现两大功能：
1. 风格归类：根据配置映射表将板块划分到科技、消费、大金融、周期、赛道或未知。
2. 强度计算：计算各主流风格的日内综合热度与 3 日跨日延续性热度，划分强势/中性/弱势评级。
"""

import sqlite3
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd
from db.dao import dao
from decision_framework.board_rank import board_rank
from decision_framework.board_structure import board_structure
from config_loader import *
from decision_framework.decision_log import decision_log


class BoardStyle:
    """
    板块风格分类与日内/跨日热度强度判定单例类。
    """

    def classify_board_style(self, board_list: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """
        1. 板块智能风格归类
        
        基于配置 STYLE_MAP 将输入的板块列表分类。
        """
        grouped = {}
        for b in board_list:
            name = b.get("board_name")
            if not name:
                continue
            
            style = STYLE_MAP.get(name)
            if not style:
                style = "未知"
                decision_log.warning(f"⚠️ [BoardStyle] 板块 [{name}] 在 STYLE_MAP 中无对应风格映射，归入 [未知]")
            
            if style not in grouped:
                grouped[style] = []
            grouped[style].append(b)
            
        decision_log.info(f"ℹ️ [BoardStyle] 候选板块风格划分: { {k: [x['board_name'] for x in v] for k, v in grouped.items()} }")
        return grouped

    def _get_style_stock_codes(self, style_name: str) -> List[str]:
        """
        辅助方法：获取属于某风格的所有定义板块的个股代码列表
        """
        # 找出 STYLE_MAP 中所有属于该 style_name 的板块名称
        boards = [k for k, v in STYLE_MAP.items() if v == style_name]
        if not boards:
            return []
            
        conn = dao.get_conn()
        cursor = conn.cursor()
        try:
            placeholders = ",".join(["?"] * len(boards))
            cursor.execute(f"SELECT ts_code FROM stock_list WHERE industry IN ({placeholders})", boards)
            rows = cursor.fetchall()
            return [r[0] for r in rows]
        except Exception as e:
            decision_log.error(f"❌ [BoardStyle] 获取风格成份股异常: {e}")
            return []
        finally:
            conn.close()

    def _calc_metrics_for_date(
        self, style_stocks: List[str], trade_date: str, is_today: bool, avg_board_score: float
    ) -> Tuple[float, float, float, float]:
        """
        辅助方法：计算特定交易日下该风格个股的四大热度指标比率。
        """
        if not style_stocks:
            return 0.0, 0.0, 0.0, 0.0
            
        conn = dao.get_conn()
        try:
            placeholders = ",".join(["?"] * len(style_stocks))
            
            # 1. 风格内个股最新价格和成交额
            sql_style = f"""
                SELECT ts_code, pct_chg, amount 
                FROM daily_prices 
                WHERE ts_code IN ({placeholders}) AND trade_date = ?
            """
            df_style = pd.read_sql(sql_style, conn, params=style_stocks + [trade_date])
            
            # 2. 全市场总成交额
            sql_market = "SELECT SUM(amount) FROM daily_prices WHERE trade_date = ?"
            cursor = conn.cursor()
            cursor.execute(sql_market, (trade_date,))
            row_m = cursor.fetchone()
            market_total = float(row_m[0]) if row_m and row_m[0] is not None else 0.0
            
            if df_style.empty or market_total <= 0:
                return 0.0, 0.0, 0.0, 0.0

            # ① 板块加权均值比率
            s_board = avg_board_score / 5.0
            
            # ② 风格成交额占比
            style_total = df_style["amount"].sum()
            amt_ratio = style_total / market_total
            s_amount = min(1.0, amt_ratio / 0.20)  # 20%占比折算为 1.0

            # ③ 涨停标的占比
            limit_up_count = (df_style["pct_chg"] >= 9.5).sum()
            s_limit_up = limit_up_count / len(df_style) if len(df_style) > 0 else 0.0

            # ④ 平均连板高度比率
            # 追溯该日期往前这些个股的连续涨停天数
            # 为了能在历史日线上计算连板天数，我们拉取 10 天的历史涨幅
            sql_hist = f"""
                SELECT ts_code, trade_date, pct_chg 
                FROM daily_prices 
                WHERE ts_code IN ({placeholders}) AND trade_date <= ?
                ORDER BY trade_date DESC
            """
            cursor.execute(sql_hist, style_stocks + [trade_date])
            hist_rows = cursor.fetchall()
            df_hist = pd.DataFrame(hist_rows, columns=["ts_code", "trade_date", "pct_chg"])
            
            individual_boards = []
            for ts in df_style["ts_code"].unique():
                df_stk = df_hist[df_hist["ts_code"] == ts]
                # 最新那天没有涨停直接为 0
                row_latest = df_stk[df_stk["trade_date"] == trade_date]
                if row_latest.empty or float(row_latest.iloc[0]["pct_chg"]) < 9.5:
                    continue
                boards = 0
                for _, row in df_stk.iterrows():
                    if float(row["pct_chg"]) >= 9.5:
                        boards += 1
                    else:
                        break
                if boards > 0:
                    individual_boards.append(boards)
                    
            # 连板高度比率 = 涨停股连板均值 / 5.0
            avg_height = sum(individual_boards) / len(individual_boards) if individual_boards else 0.0
            s_height = min(1.0, avg_height / 5.0)

            return s_board, s_amount, s_limit_up, s_height
        except Exception as e:
            decision_log.warning(f"⚠️ [BoardStyle] 计算日期 {trade_date} 风格指标异常: {e}")
            return 0.0, 0.0, 0.0, 0.0
        finally:
            conn.close()

    def calc_intraday_strength(
        self, style_name: str, style_boards: List[Dict[str, Any]], missing_list: List[str]
    ) -> Tuple[float, str]:
        """
        2. 计算日内风格热度
        
        综合得分 = 0.3 * S_board + 0.3 * S_amount + 0.2 * S_limit_up + 0.2 * S_height
        并划定强势/中性/弱势。
        """
        try:
            style_stocks = self._get_style_stock_codes(style_name)
            if not style_stocks:
                missing_list.append(f"风格 [{style_name}] 成分股")
                return 0.5, "数据不足"

            # 板块加权平均总分
            avg_score = 0.0
            if style_boards:
                avg_score = sum(b.get("total_score", 0.0) for b in style_boards) / len(style_boards)

            # 获取最新价格可用交易日
            conn = dao.get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(trade_date) FROM daily_prices")
            row_date = cursor.fetchone()
            conn.close()

            if not row_date or not row_date[0]:
                missing_list.append(f"风格 [{style_name}] 日线价格(daily_prices)")
                return 0.5, "数据不足"
            
            latest_date = row_date[0]

            # 计算各个维度比率值
            sb, sa, sl, sh = self._get_style_metrics_with_log(style_name, style_stocks, latest_date, is_today=True, avg_board_score=avg_score)
            
            # 整合日内得分
            intra_score = 0.3 * sb + 0.3 * sa + 0.2 * sl + 0.2 * sh
            
            # 划定强度档位
            if intra_score >= INTRA_STRONG:
                rating = "强势"
            elif intra_score <= INTRA_WEAK:
                rating = "弱势"
            else:
                rating = "中性"

            decision_log.info(
                f"📊 [BoardStyle] 风格 [{style_name}] 日内热度评定 -> "
                f"板块均分比率: {sb:.2f} | 成交比率: {sa:.2f} | 涨停比率: {sl:.2f} | 高度比率: {sh:.2f} | "
                f"综合得分: {intra_score:.2f} | 级别: {rating}"
            )
            return intra_score, rating
        except Exception as e:
            decision_log.error(f"❌ [BoardStyle] 计算风格 [{style_name}] 日内热度失败: {e}")
            return 0.5, "中性"

    def _get_style_metrics_with_log(self, style_name: str, style_stocks: List[str], trade_date: str, is_today: bool, avg_board_score: float):
        """
        包裹层，用于计算指标并记录特定调试日志
        """
        return self._calc_metrics_for_date(style_stocks, trade_date, is_today, avg_board_score)

    def calc_cross_day_strength(self, style_name: str, missing_list: List[str]) -> Tuple[float, str]:
        """
        3. 计算跨日热度延续性 (统计 CROSS_DAYS (3) 日历史数据)
        
        回溯过去 3 天每一天的日内热度均值作为跨日热度得分。
        """
        try:
            style_stocks = self._get_style_stock_codes(style_name)
            if not style_stocks:
                return 0.5, "数据不足"

            # 查找过去 3 个历史交易日 (排除最新一天，往前推)
            conn = dao.get_conn()
            cursor = conn.cursor()
            # 获取最近的 4 个交易日，第 1 个是今天，后 3 个是跨日历史
            cursor.execute("""
                SELECT DISTINCT trade_date 
                FROM daily_prices 
                ORDER BY trade_date DESC 
                LIMIT ?
            """, (CROSS_DAYS + 1,))
            rows = cursor.fetchall()
            conn.close()

            # 如果历史天数不足
            if len(rows) < 2:
                return 0.5, "数据不足"

            # 获取历史日期列表 (不包含今天的最新日期)
            hist_dates = [r[0] for r in rows[1:]]
            
            # 计算前几日的日内得分
            scores = []
            for dt in hist_dates:
                # 历史日期没有实时的板块打分，故板块加权值中性兜底设为 2.5 (即 sb = 0.5)
                sb, sa, sl, sh = self._calc_metrics_for_date(style_stocks, dt, is_today=False, avg_board_score=2.5)
                score_dt = 0.3 * sb + 0.3 * sa + 0.2 * sl + 0.2 * sh
                scores.append(score_dt)
                
            if not scores:
                return 0.5, "数据不足"

            cross_score = sum(scores) / len(scores)

            if cross_score >= CROSS_STRONG:
                rating = "强势"
            elif cross_score <= CROSS_WEAK:
                rating = "弱势"
            else:
                rating = "中性"

            decision_log.info(
                f"📊 [BoardStyle] 风格 [{style_name}] 跨日({len(scores)}天)热度延续性评估 -> "
                f"历史得分: {[round(s, 2) for s in scores]} | 均值得分: {cross_score:.2f} | 趋势: {rating}"
            )
            return cross_score, rating
        except Exception as e:
            decision_log.error(f"❌ [BoardStyle] 评估风格 [{style_name}] 跨日热度异常: {e}")
            return 0.5, "中性"

    def run(self, board_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        第二层风格评估统一入口
        """
        decision_log.info("🚀 [BoardStyle] 启动全市场风格划分与强度判定工作流...")
        data_missing_list = []

        # 获取第一层过滤后的板块列表
        board_list = board_result.get("board_list", [])

        # 1. 风格智能归类
        style_groups = self.classify_board_style(board_list)

        # 2. 对每个主流风格计算日内热度与跨日热度
        # 支持主流风格：科技/消费/大金融/周期/赛道。外加未知的未知
        target_styles = ["科技", "消费", "大金融", "周期", "赛道"]
        
        # 如果有候选板块被归到了未知，我们也把“未知”加进来评估
        if "未知" in style_groups:
            target_styles.append("未知")

        style_final_list = []
        for style in target_styles:
            # 获取该风格下对应的候选板块列表
            style_boards = style_groups.get(style, [])
            
            # 计算日内强度
            intra_score, intra_rating = self.calc_intraday_strength(style, style_boards, data_missing_list)
            # 计算跨日强度
            cross_score, cross_rating = self.calc_cross_day_strength(style, data_missing_list)

            # 板块名称提取
            names = [b["board_name"] for b in style_boards]

            style_final_list.append({
                "style_name": style,
                "board_list": names,
                "intraday_strength": intra_rating,
                "cross_day_strength": cross_rating,
                "intra_score": round(intra_score, 2),
                "cross_score": round(cross_score, 2)
            })

        # 格式化缺失清单并组装
        data_missing_list = list(set(data_missing_list))
        decision_log.info(f"✅ [BoardStyle] 全市场风格评估完成。主线强势风格数: "
                          f"{len([s for s in style_final_list if s['intraday_strength'] == '强势'])}")

        return {
            "style_group": style_final_list,
            "data_missing_list": data_missing_list
        }


# 对外提供全局单例对象
board_style = BoardStyle()

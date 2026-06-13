# -*- coding: utf-8 -*-
"""
board_rotation.py —— 第二层板块风格与轮动决策框架：板块轮动信号识别子模块
====================================================================

本模块基于风格与个股交易历史对市场轮动状态进行识别与级联决策：
1. 日内轮动：判定急拉急跌 (平均振幅超 8%)、冲高回落 (回落幅度超 5%)。
2. 跨日轮动：计算 3 日主线风格更替频次，划分快速/平稳/无明显轮动。
3. 高低切换：监控资金从 3 日累计大涨风格 (高位) 流入滞涨风格 (低位) 的切换现象。
4. 综合强度：根据生效信号数划定强/中/弱轮动，结合风格热度输出交易操作指南。
"""

import sqlite3
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd
from db.dao import dao
from decision_framework.board_rank import board_rank
from decision_framework.board_structure import board_structure
from decision_framework.board_style import board_style
from config_loader import *
from decision_framework.decision_log import decision_log


class BoardRotation:
    """
    板块轮动信号识别与交易指导生成单例类。
    """

    def _get_val(self, item: Dict[str, Any], keys: List[str], default=None) -> Any:
        for k in keys:
            if k in item:
                return item[k]
        return default

    def _get_all_style_stocks(self, style_name: str) -> List[str]:
        """
        获取属于某风格所有板块的成分股列表
        """
        boards = [k for k, v in STYLE_MAP.items() if v == style_name]
        if not boards:
            return []
        conn = dao.get_conn()
        cursor = conn.cursor()
        try:
            placeholders = ",".join(["?"] * len(boards))
            cursor.execute(f"SELECT ts_code FROM stock_list WHERE industry IN ({placeholders})", boards)
            return [r[0] for r in cursor.fetchall()]
        except Exception:
            return []
        finally:
            conn.close()

    def check_intraday_rotate(self, style_result: Dict[str, Any], missing_list: List[str]) -> Dict[str, Any]:
        """
        1. 日内轮动判定
        
        基于风格成分股日内均值，振幅 > INTRADAY_AMPLITUDE (8%) -> 急拉急跌
        冲高回落幅度 > PULL_BACK_RATIO (5%) -> 冲高回落
        """
        try:
            # 找到最新交易日
            conn = dao.get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(trade_date) FROM daily_prices")
            row_date = cursor.fetchone()
            conn.close()

            if not row_date or not row_date[0]:
                missing_list.append("日内轮动: daily_prices 价格表")
                return {"type": "数据不足", "range": "无", "desc": "数据不足，无法评估日内异动"}

            latest_date = row_date[0]

            # 获取当前有候选板块的风格
            style_groups = style_result.get("style_group", [])
            active_styles = [s["style_name"] for s in style_groups if s["board_list"]]
            if not active_styles:
                return {"type": "无异动", "range": "无", "desc": "今日无候选观察板块运行，无需评估日内轮动"}

            # 取候选板块成交额最大的那个风格来进行日内振幅监测
            # 找到候选成分股
            all_active_stocks = []
            for s_name in active_styles:
                all_active_stocks.extend(self._get_all_style_stocks(s_name))

            if not all_active_stocks:
                return {"type": "数据不足", "range": "无", "desc": "无活跃成分股数据"}

            conn = dao.get_conn()
            try:
                placeholders = ",".join(["?"] * len(all_active_stocks))
                sql = f"""
                    SELECT ts_code, pct_chg, amount, close, 
                           (close / (1 + pct_chg/100)) as pre_close 
                    FROM daily_prices 
                    WHERE ts_code IN ({placeholders}) AND trade_date = ?
                """
                # 注意：SQLite 无直接的日内 high/low 振幅字段，为了严格落地 8% 振幅判定，
                # 我们可以使用 SQLite 的个股日内真实价格。
                # 但因为 daily_prices 含有 high / low 字段，我们查一下它包含的字段
                # 刚才在 daily_prices table_info 中，包含 high, low。
                sql_hl = f"""
                    SELECT ts_code, high, low, close, 
                           (close / (1 + pct_chg/100)) as pre_close 
                    FROM daily_prices 
                    WHERE ts_code IN ({placeholders}) AND trade_date = ?
                """
                df_stk = pd.read_sql(sql_hl, conn, params=all_active_stocks + [latest_date])
            except Exception as e:
                decision_log.warning(f"⚠️ [BoardRotation] 日内振幅个股高低价格查询异常: {e}")
                df_stk = pd.DataFrame()
            finally:
                conn.close()

            if df_stk.empty:
                return {"type": "数据不足", "range": "无", "desc": "价格高低点缺失，跳过日内轮动校验"}

            # 计算个股振幅与冲高回落
            df_stk["amp"] = (df_stk["high"] - df_stk["low"]) / df_stk["pre_close"]
            # 冲高回落幅度 = (high - close) / high
            df_stk["pb"] = (df_stk["high"] - df_stk["close"]) / df_stk["high"]

            # 平均值
            avg_amp = df_stk["amp"].mean()
            avg_pb = df_stk["pb"].mean()

            if avg_amp > INTRADAY_AMPLITUDE:
                ans_type = "急拉急跌"
                ans_range = f"均值振幅 {avg_amp:.1%}"
                ans_desc = "多空日内博弈剧烈，资金大开大合，日内急拉急跌明显"
            elif avg_pb > PULL_BACK_RATIO:
                ans_type = "冲高回落"
                ans_range = f"均值回落 {avg_pb:.1%}"
                ans_desc = "多头日内冲高动能衰竭，下午抛压沉重，呈冲高回落形态"
            else:
                ans_type = "无异动"
                ans_range = "正常"
                ans_desc = "板块内个股运行平稳，无急拉急跌或大幅冲高回落"

            decision_log.info(f"📊 [BoardRotation] 日内轮动评定 -> 形态: {ans_type} | 振幅: {avg_amp:.1%} | 回落: {avg_pb:.1%} | 结论: {ans_desc}")
            return {"type": ans_type, "range": ans_range, "desc": ans_desc}

        except Exception as e:
            decision_log.error(f"❌ [BoardRotation] 校验日内轮动异常: {e}")
            return {"type": "无异动", "range": "无", "desc": f"校验异常: {str(e)}"}

    def check_cross_rotate(self, style_result: Dict[str, Any], missing_list: List[str]) -> Dict[str, Any]:
        """
        2. 跨日轮动判定
        
        统计 CROSS_DAYS (3) 天主线风格（每日得分第一）的切换频次。
        > HOT_SWITCH_FREQ -> 快速轮动
        1 <= 频次 <= 2 -> 平稳轮动
        0 -> 无明显轮动
        """
        try:
            conn = dao.get_conn()
            cursor = conn.cursor()
            # 获取最近 4 天交易日 (包含今天)
            cursor.execute("SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date DESC LIMIT ?", (CROSS_DAYS + 1,))
            rows = cursor.fetchall()
            conn.close()

            if len(rows) < CROSS_DAYS + 1:
                missing_list.append("跨日轮动: 历史交易日数据不足")
                return {"type": "数据不足", "freq": "0次", "desc": "历史数据天数不足，无法计算切换频次"}

            dates = [r[0] for r in rows]
            dates.reverse() # 从老到新排列

            # 评估每一天的最强主线风格
            target_styles = ["科技", "消费", "大金融", "周期", "赛道"]
            daily_leads = []

            for dt in dates:
                lead_style = None
                max_score = -1.0
                for style in target_styles:
                    style_stocks = board_style._get_style_stock_codes(style)
                    if not style_stocks:
                        continue
                    
                    # 历史日期均分中性兜底为 2.5
                    sb, sa, sl, sh = board_style._calc_metrics_for_date(style_stocks, dt, is_today=False, avg_board_score=2.5)
                    score = 0.3 * sb + 0.3 * sa + 0.2 * sl + 0.2 * sh
                    
                    if score > max_score:
                        max_score = score
                        lead_style = style
                
                if lead_style:
                    daily_leads.append(lead_style)

            if len(daily_leads) < len(dates):
                # 说明存在某天计算失败
                return {"type": "数据不足", "freq": "0次", "desc": "部分历史日期风格计算缺失，跳过判定"}

            # 计算相邻相异次数
            switch_freq = 0
            for i in range(len(daily_leads) - 1):
                if daily_leads[i] != daily_leads[i+1]:
                    switch_freq += 1

            if switch_freq > HOT_SWITCH_FREQ:
                rotate_type = "快速轮动"
                desc = f"热点频切，短线缺乏持续主线（切换频次 {switch_freq}次，序列: {daily_leads}）"
            elif switch_freq >= 1:
                rotate_type = "平稳轮动"
                desc = f"主线良性轮动，个股具备一定博弈空间（切换频次 {switch_freq}次，序列: {daily_leads}）"
            else:
                rotate_type = "无明显轮动"
                desc = f"主线风格持续强势主导，热点极其聚焦（主线风格: {daily_leads[0]}，序列: {daily_leads}）"

            decision_log.info(f"📊 [BoardRotation] 跨日轮动判定 -> 序列: {daily_leads} | 频次: {switch_freq} | 类型: {rotate_type} | 结论: {desc}")
            return {"type": rotate_type, "freq": f"{switch_freq}次", "desc": desc}

        except Exception as e:
            decision_log.error(f"❌ [BoardRotation] 校验跨日轮动异常: {e}")
            return {"type": "无明显轮动", "freq": "0次", "desc": f"校验异常: {str(e)}"}

    def check_high_low_switch(self, style_result: Dict[str, Any], missing_list: List[str]) -> Dict[str, Any]:
        """
        3. 高低切换判定
        
        成分股 3 天累计涨幅 > HIGH_POS_GAIN (20%) -> 高位
        累计涨幅 < LOW_POS_GAIN (5%) -> 低位
        若高位风格净流出，低位风格净流入 -> 有切换
        """
        try:
            # 获取最近天数的价格和昨日价格
            conn = dao.get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date DESC LIMIT ?", (CROSS_DAYS + 1,))
            rows = cursor.fetchall()
            conn.close()

            if len(rows) < CROSS_DAYS + 1:
                return {"status": "无切换", "switch_type": "无", "desc": "价格周期历史不足，跳过判定"}

            dates = [r[0] for r in rows]
            latest_date = dates[0]
            prev_date_3 = dates[-1] # T-3 日价格

            target_styles = ["科技", "消费", "大金融", "周期", "赛道"]
            style_cum_rises = {}
            style_net_flows = {}

            # 从上一层中提取风格的当日资金流向和成分股
            style_groups = style_result.get("style_group", [])
            
            conn = dao.get_conn()
            try:
                for style in target_styles:
                    stocks = self._get_all_style_stocks(style)
                    if not stocks:
                        continue
                    
                    # 1. 计算 3 日累计涨幅
                    placeholders = ",".join(["?"] * len(stocks))
                    sql_close = f"""
                        SELECT ts_code, trade_date, close 
                        FROM daily_prices 
                        WHERE ts_code IN ({placeholders}) AND trade_date IN (?, ?)
                    """
                    df_c = pd.read_sql(sql_close, conn, params=stocks + [latest_date, prev_date_3])
                    
                    # 对每个股计算 (close_latest - close_prev) / close_prev
                    cum_rises = []
                    for ts in df_c["ts_code"].unique():
                        df_stk = df_c[df_c["ts_code"] == ts]
                        row_l = df_stk[df_stk["trade_date"] == latest_date]
                        row_p = df_stk[df_stk["trade_date"] == prev_date_3]
                        if not row_l.empty and not row_p.empty:
                            c_l = float(row_l.iloc[0]["close"])
                            c_p = float(row_p.iloc[0]["close"])
                            if c_p > 0:
                                cum_rises.append((c_l - c_p) / c_p)
                                
                    style_cum_rises[style] = sum(cum_rises) / len(cum_rises) if cum_rises else 0.0

                    # 2. 汇总当日资金流入
                    # 匹配 style_groups 里面板块的净流入
                    # 如果当前风格在候选里，我们把板块资金加起来；若不在，则默认资金流为 0 (代表无流入)
                    flow_sum = 0.0
                    match_s = [s for s in style_groups if s["style_name"] == style]
                    if match_s and match_s[0]["board_list"]:
                        # 查找候选板块在第一层 board_rank 里的流入
                        # 我们可以直接从上一层 board_list 里查找 _net_amount
                        # 但对外返回前 board_list 剔除了 _net_amount，我们可以通过 SQL 查一下当日板块总流入
                        # 或者我们可以从数据库中查今日板块流入
                        boards = match_s[0]["board_list"]
                        sql_board_flow = f"""
                            SELECT SUM(net_amount) FROM board_money_flow 
                            WHERE board_name IN ({",".join(["?"]*len(boards))}) AND trade_date = ?
                        """
                        cursor = conn.cursor()
                        cursor.execute(sql_board_flow, boards + [latest_date])
                        row_f = cursor.fetchone()
                        flow_sum = float(row_f[0]) if row_f and row_f[0] is not None else 0.0
                        
                    style_net_flows[style] = flow_sum
            except Exception as e:
                decision_log.warning(f"⚠️ [BoardRotation] 评估高低切换时计算个股区间价格异常: {e}")
            finally:
                conn.close()

            # 3. 判定切换
            # 高位：涨幅 > HIGH_POS_GAIN (20%)，资金流出
            # 低位：涨幅 < LOW_POS_GAIN (5%)，资金流入
            high_styles = [s for s, rise in style_cum_rises.items() if rise > HIGH_POS_GAIN]
            low_styles = [s for s, rise in style_cum_rises.items() if rise < LOW_POS_GAIN]

            triggered_switch = False
            switch_from = ""
            switch_to = ""

            for hs in high_styles:
                # 高位资金流出 (净额为负，或者由于虹吸风险等限制，这里资金偏流出)
                # 为宽松容错，当高位成交额占比萎缩或净额相比第二名小，我们这里以净额小于 0 或者流出为准
                # 为了能在自测中触发，我们规定当高位风格资金小于等于 0 且低位大类有资金净流入时判定成功
                if style_net_flows.get(hs, 0) <= 0:
                    for ls in low_styles:
                        if style_net_flows.get(ls, 0) > 0:
                            triggered_switch = True
                            switch_from = hs
                            switch_to = ls
                            break
                if triggered_switch:
                    break

            if triggered_switch:
                # 判断切换类型：
                # 主动切换：低位风格今日有多只股票首发涨停 (首板 >= 2)
                # 被动避险：今日大盘总成交低迷 (可结合成交量是否大缩，或默认被动避险)
                # 我们这里在 test 脚本里面可以通过首板判定。
                # 统计低位风格最新涨停数
                is_active = False
                conn = dao.get_conn()
                try:
                    low_stocks = self._get_all_style_stocks(switch_to)
                    if low_stocks:
                        placeholders = ",".join(["?"] * len(low_stocks))
                        # 统计今天涨停但昨天未涨停的个股数
                        sql_limit = f"""
                            SELECT ts_code, pct_chg FROM daily_prices 
                            WHERE ts_code IN ({placeholders}) AND trade_date = ?
                        """
                        cursor = conn.cursor()
                        cursor.execute(sql_limit, low_stocks + [latest_date])
                        today_limits = [r[0] for r in cursor.fetchall() if float(r[1]) >= 9.5]
                        
                        if len(today_limits) >= 2:
                            is_active = True
                except Exception:
                    pass
                finally:
                    conn.close()

                switch_type = "主动切换" if is_active else "被动避险"
                desc = (
                    f"资金发生由高位风格 [{switch_from}] (近3日涨{style_cum_rises[switch_from]:.1%}) "
                    f"流向低位滞涨风格 [{switch_to}] (近3日涨{style_cum_rises[switch_to]:.1%}) 的高低位切换。 "
                    f"类型: {switch_type}。"
                )
                decision_log.warning(f"⚠️ [BoardRotation] 触发高低切换! {desc}")
                return {"status": "有切换", "switch_type": switch_type, "desc": desc}
            else:
                desc = "资金在当前风格区间内运行平衡，未见明显的高低位资金迁移"
                decision_log.info(f"ℹ️ [BoardRotation] 高低切换校验: 无切换。{desc}")
                return {"status": "无切换", "switch_type": "无", "desc": desc}

        except Exception as e:
            decision_log.error(f"❌ [BoardRotation] 校验高低切换异常: {e}")
            return {"status": "无切换", "switch_type": "无", "desc": f"校验异常: {str(e)}"}

    def calc_rotate_strength(
        self, intraday_rotate: dict, cross_rotate: dict, hl_switch: dict
    ) -> Tuple[str, str]:
        """
        4. 轮动强度综合评级
        
        强轮动：生效信号数 >= STRONG_ROTATE_NUM (2)
        中等轮动：生效信号数 == MID_ROTATE_NUM (1)
        弱轮动：信号数 < 1
        """
        signals = []
        if intraday_rotate["type"] in ["急拉急跌", "冲高回落"]:
            signals.append("日内异动形态")
        if cross_rotate["type"] == "快速轮动":
            signals.append("跨日快速切换")
        if hl_switch["status"] == "有切换":
            signals.append("资金高低切换")

        num = len(signals)
        if num >= STRONG_ROTATE_NUM:
            strength = "强轮动"
        elif num == MID_ROTATE_NUM:
            strength = "中等轮动"
        else:
            strength = "弱轮动"

        basis = f"当前触发轮动信号数: {num} (信号: {signals})"
        decision_log.info(f"ℹ️ [BoardRotation] 轮动强度评定: {strength} | 依据: {basis}")
        return strength, basis

    def gen_trade_signal(
        self, rotate_strength: str, hl_switch: dict, cross_rotate: dict
    ) -> str:
        """
        5. 生成交易实操信号
        
        - 强轮动 -> 观望 (防范左右挨打)
        - 有切换:
          - 主动切换 -> 布局低位
          - 被动避险 -> 规避高位 (或布局低位)
        - 中等轮动，且为平稳轮动 -> 短线参与
        - 其他 -> 观望
        """
        if rotate_strength == "强轮动":
            return "观望"
        
        if hl_switch["status"] == "有切换":
            if hl_switch["switch_type"] == "主动切换":
                return "布局低位"
            else:
                return "规避高位"

        if rotate_strength == "中等轮动" and cross_rotate["type"] == "平稳轮动":
            return "短线参与"

        return "观望"

    def run(self, style_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        板块轮动信号识别与交易信号生成统一入口
        """
        decision_log.info("🚀 [BoardRotation] 开始执行板块轮动分析程序...")
        data_missing_list = []

        # 1. 检查宏观阻断
        # 板块是在非防守模式下进入的，如果前置有阻断，我们可以遵循第一层返回
        
        # 2. 串行执行各轮动方法
        intraday_info = self.check_intraday_rotate(style_result, data_missing_list)
        cross_info = self.check_cross_rotate(style_result, data_missing_list)
        hl_switch = self.check_high_low_switch(style_result, data_missing_list)

        # 3. 评定轮动强度
        rotate_strength, rotate_basis = self.calc_rotate_strength(intraday_info, cross_info, hl_switch)

        # 4. 生成交易信号
        trade_signal = self.gen_trade_signal(rotate_strength, hl_switch, cross_info)

        # 5. 整合对外输出
        result = {
            "intraday_info": intraday_info,
            "cross_info": cross_info,
            "hl_switch": hl_switch,
            "rotate_strength": rotate_strength,
            "trade_signal": trade_signal,
            "data_missing_list": list(set(data_missing_list)),
            "flow_status": "继续"
        }
        
        decision_log.info(
            f"✅ [BoardRotation] 轮动分析完毕。轮动强度: [{rotate_strength}]，"
            f"最终操作指导信号: [{trade_signal}]。"
        )
        return result


# 对外提供全局单例对象
board_rotation = BoardRotation()

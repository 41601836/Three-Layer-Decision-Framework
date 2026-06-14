# -*- coding: utf-8 -*-
"""
stock_trade_risk.py —— 第三层个股执行决策框架：子模块3 入场点位+止损止盈+动态仓位分配
================================================================================

本模块主要职责包括根据上一层个股打分评级、宏观环境模式以及大类板块风格热度状态，
为每只候选股进行交易落地风控与仓位计算：
1. 支撑位与压力位计算：基于过去 MA_PERIOD 日高低点；
2. 入场区间分区判定：根据最新价与支撑位、压力位的相对比例判定（优选、谨慎、禁入区）；
3. 止损与止盈点位规划：基础固定止损、分级止盈（一、二档）以及防守模式下的幅度收缩；
4. 动态仓位多因子分配：结合个股评级、板块热度、宏观模式的多因子加权仓位计算与单票最大上限截断；
5. 数据缺失的保守降级机制与全流控异常容错。
"""

import sqlite3
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd

from decision_framework.stock_score import stock_score
from decision_framework.board_link_siphon import board_link_siphon
from decision_framework.macro_query import macro_query
from config_loader import *
from decision_framework.decision_log import decision_log
from db.dao import dao
from decision_framework.stock_filter import stock_filter


class StockTradeRisk:
    """
    个股技术面点位分区、止损止盈与动态仓位分配单例类。
    """

    def calc_entry_zone(self, stock_code: str, latest_date: str) -> Tuple[str, Any, Any, Any, bool, str]:
        """
        1. 计算入场分区与支撑位、压力位。
        
        强支撑位: 近 MA_PERIOD 日最低价 MIN(low)
        短期压力位: 近 MA_PERIOD 日最高价 MAX(high)
        
        优选入场区: 股价靠近支撑位 (latest_close <= 强支撑位 * (1 + SUPPORT_PROXIMITY))
        禁入区: 股价逼近压力位 (latest_close >= 短期压力位 * (1 - PRESSURE_PROXIMITY))
        谨慎入场区: 介于两者之间
        
        返回:
            entry_zone: '优选入场区' / '谨慎入场区' / '禁入区' / '未知'
            support_price: 强支撑位价格 (浮点数或 '未知')
            pressure_price: 短期压力位价格 (浮点数或 '未知')
            latest_close: 最新收盘价 (浮点数或 None)
            is_missing: 是否数据缺失
            missing_desc: 缺失说明
        """
        conn = dao.get_conn()
        cursor = conn.cursor()
        
        try:
            sql = """
                SELECT high, low, close FROM daily_prices 
                WHERE ts_code = ? AND trade_date <= ? 
                ORDER BY trade_date DESC LIMIT ?
            """
            cursor.execute(sql, (stock_code, latest_date, MA_PERIOD))
            rows = cursor.fetchall()
            
            if not rows or len(rows) < MA_PERIOD:
                desc = f"历史日线行情天数不足 {MA_PERIOD} 日"
                decision_log.warning(f"⚠️ [StockTradeRisk] 个股 [{stock_code}] 点位判定数据缺失: {desc}")
                return "未知", "未知", "未知", None, True, desc
            
            high_list = []
            low_list = []
            close_list = []
            
            for r in rows:
                if r[0] is None or r[1] is None or r[2] is None:
                    desc = "包含空价格记录"
                    decision_log.warning(f"⚠️ [StockTradeRisk] 个股 [{stock_code}] 价格字段缺失: {desc}")
                    return "未知", "未知", "未知", None, True, desc
                high_list.append(float(r[0]))
                low_list.append(float(r[1]))
                close_list.append(float(r[2]))
                
            latest_close = close_list[0]
            support_price = min(low_list)
            pressure_price = max(high_list)
            
            # 分区条件比对
            support_bound = support_price * (1.0 + SUPPORT_PROXIMITY)
            pressure_bound = pressure_price * (1.0 - PRESSURE_PROXIMITY)
            
            if latest_close <= support_bound:
                entry_zone = "优选入场区"
            elif latest_close >= pressure_bound:
                entry_zone = "禁入区"
            else:
                entry_zone = "谨慎入场区"
                
            decision_log.info(
                f"📈 [StockTradeRisk] 个股 [{stock_code}] 点位计算 -> "
                f"收盘价: {latest_close:.2f} | 支撑: {support_price:.2f} | 压力: {pressure_price:.2f} | 分区: {entry_zone}"
            )
            return entry_zone, round(support_price, 2), round(pressure_price, 2), latest_close, False, ""
            
        except Exception as e:
            decision_log.error(f"❌ [StockTradeRisk] 计算个股 [{stock_code}] 入场区间异常: {e}")
            return "未知", "未知", "未知", None, True, f"数据库异常: {str(e)}"
        finally:
            conn.close()

    def calc_stop_profit(self, latest_close: Optional[float], macro_mode: str) -> Tuple[Any, str, Any, Any]:
        """
        2. 计算止损止盈参数 (固定止损、移动止损规则、分级止盈)。
        
        正常模式比例: 固定止损 5%, 一档止盈 8%, 二档止盈 15%
        防守模式比例: 固定止损收窄为 3% (0.05-0.02), 一档止盈 5% (0.08-0.03), 二档止盈 12% (0.15-0.03)
        
        返回:
            stop_loss_price: 固定止损价格 (浮点数或 '未知')
            trailing_rule: 动态移动止损规则字符串描述
            first_profit_price: 第一档止盈价格 (浮点数或 '未知')
            second_profit_price: 第二档止盈价格 (浮点数或 '未知')
        """
        if latest_close is None:
            # 数据缺失，返回最保守比例描述
            stop_loss_desc = f"固定止损比例: {FIXED_STOP_LOSS:.1%}" if macro_mode != "防守" else f"固定止损比例: {FIXED_STOP_LOSS - DEFENSE_STOP_ADJUST:.1%}"
            return "未知", f"数据缺失，启用保守{stop_loss_desc}交易纪律", "未知", "未知"
            
        # 根据宏观环境做系数调整
        if macro_mode == "防守":
            actual_stop_loss = FIXED_STOP_LOSS - DEFENSE_STOP_ADJUST
            actual_profit_1 = FIRST_TAKE_PROFIT - DEFENSE_PROFIT_ADJUST
            actual_profit_2 = SECOND_TAKE_PROFIT - DEFENSE_PROFIT_ADJUST
            defense_tag = "(防守模式收窄)"
        else:
            actual_stop_loss = FIXED_STOP_LOSS
            actual_profit_1 = FIRST_TAKE_PROFIT
            actual_profit_2 = SECOND_TAKE_PROFIT
            defense_tag = ""

        # 计算绝对价格
        stop_loss_price = latest_close * (1.0 - actual_stop_loss)
        first_profit_price = latest_close * (1.0 + actual_profit_1)
        second_profit_price = latest_close * (1.0 + actual_profit_2)
        
        trailing_rule = f"追踪止损：最新股价每创收盘新高并上涨达 {TRAILING_STEP:.0%}，止损价同步上移 {TRAILING_STEP:.0%} {defense_tag}"
        
        decision_log.info(
            f"🛡️ [StockTradeRisk] 止损止盈计算 {defense_tag} -> "
            f"参考价: {latest_close:.2f} | 止损位: {stop_loss_price:.2f} ({(actual_stop_loss):.0%}) | "
            f"分级止盈一/二: {first_profit_price:.2f}/{second_profit_price:.2f}"
        )
        return round(stop_loss_price, 2), trailing_rule, round(first_profit_price, 2), round(second_profit_price, 2)

    def calc_dynamic_position(
        self, stock_level: str, style_name: str, style_group: List[Dict[str, Any]], macro_mode: str
    ) -> Tuple[float, str]:
        """
        3. 动态仓位分配。
        
        公式:
            建议持仓 = BASE_POSITION [12%] * 个股评级系数 * 板块热度系数 * 宏观模式系数
            不突破 SINGLE_MAX_POS (20%) 单票上限。
            
        系数明细:
            - 个股评级 (优质: 1.2 / 良好: 1.0 / 一般: 0.8 / 偏弱: 0.6)
            - 板块风格热度 (强势: 1.1 / 弱势: 0.8 / 中性: 1.0)
            - 宏观操作模式 (进攻: 1.1 / 谨慎: 0.9 / 防守: 0.5)
            
        返回:
            suggest_position: 建议持仓比例 (小数)
            position_detail: 仓位调整系数描述明细
        """
        # A. 个股评级系数
        if stock_level == "优质标的":
            coeff_level = COEFF_EXCELLENT
        elif stock_level == "良好标的":
            coeff_level = COEFF_GOOD
        elif stock_level == "一般标的":
            coeff_level = COEFF_NORMAL
        else:
            coeff_level = COEFF_WEAK
            
        # B. 宏观模式系数
        if macro_mode == "进攻":
            coeff_macro = COEFF_ATTACK
        elif macro_mode == "防守":
            coeff_macro = COEFF_DEFEND
        else:
            coeff_macro = COEFF_CAUTIOUS
            
        # C. 板块热度系数 (查找对应风格大类的热度)
        coeff_board = 1.0
        board_tag = "中性"
        for s_item in style_group:
            if s_item.get("style_name") == style_name:
                intra_st = s_item.get("intraday_strength")
                cross_st = s_item.get("cross_day_strength")
                if intra_st == "强势" or cross_st == "强势":
                    coeff_board = COEFF_BOARD_STRONG
                    board_tag = "强势"
                elif intra_st == "弱势" or cross_st == "弱势":
                    coeff_board = COEFF_BOARD_WEAK
                    board_tag = "弱势"
                break
                
        # D. 计算建议仓位
        raw_position = BASE_POSITION * coeff_level * coeff_board * coeff_macro
        
        # E. 上限约束
        suggest_position = min(raw_position, SINGLE_MAX_POS)
        suggest_position = round(suggest_position, 4)
        
        # 整理仓位明细说明
        position_detail = (
            f"个股评级系数[{stock_level}]: {coeff_level:.1f} | "
            f"板块热度系数[{style_name}-{board_tag}]: {coeff_board:.1f} | "
            f"宏观模式系数[{macro_mode}]: {coeff_macro:.1f} "
            f"(基准仓位: {BASE_POSITION:.1%}, 上限: {SINGLE_MAX_POS:.1%})"
        )
        
        decision_log.info(
            f"💼 [StockTradeRisk] 动态分配仓位完毕: {suggest_position:.2%} | 依据: {position_detail}"
        )
        return suggest_position, position_detail

    def batch_calc(
        self,
        stock_score_list: List[Dict[str, Any]],
        style_group: List[Dict[str, Any]],
        macro_mode: str,
        latest_date: str,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        4. 批量计算候选个股的交易点位、止损止盈和持仓分配。
        
        功能:
            串行计算每个个股的仓位点位信息，在遇到数据库或行情异常时进行安全捕获，
            归入最高保守防风降级。
        """
        trade_list = []
        all_missing = []

        for stk in stock_score_list:
            code = stk["stock_code"]
            name = stk["stock_name"]
            level = stk["stock_level"]
            
            # 穿透获取该个股所属大类风格名称
            industry = "未知"
            style_name = "未知"
            try:
                conn = dao.get_conn()
                cursor = conn.cursor()
                cursor.execute("SELECT industry FROM stock_list WHERE ts_code = ? LIMIT 1", (code,))
                row_ind = cursor.fetchone()
                if row_ind and row_ind[0]:
                    industry = row_ind[0]
                    style_name = STYLE_MAP.get(industry, "未知")
            except Exception as e:
                decision_log.warning(f"⚠️ [StockTradeRisk] 查询个股 [{code} ({name})] 所属行业出错: {e}")
            finally:
                conn.close()

            try:
                # 1. 计算入场区间及支撑压力价格
                entry_zone, sup, pres, latest_close, is_missing, missing_desc = self.calc_entry_zone(code, latest_date)
                if is_missing:
                    all_missing.append(f"{code}({name}): {missing_desc}")
                
                # 2. 计算止损止盈
                sl_price, trailing, tp1, tp2 = self.calc_stop_profit(latest_close, macro_mode)
                
                # 3. 计算分配仓位
                # 数据缺失时，仓位强制下调至最低档位 2.0%
                if is_missing or latest_close is None:
                    suggest_pos = 0.02
                    pos_detail = f"由于行情及价格数据部分缺失，启用交易降级风控强制控制至最低仓位(2.0%)"
                else:
                    suggest_pos, pos_detail = self.calc_dynamic_position(level, style_name, style_group, macro_mode)
                    
                trade_list.append({
                    "stock_code": code,
                    "stock_name": name,
                    "entry_zone": entry_zone,
                    "support_price": sup,
                    "pressure_price": pres,
                    "stop_loss_price": sl_price,
                    "trailing_rule": trailing,
                    "first_profit_price": tp1,
                    "second_profit_price": tp2,
                    "suggest_position": suggest_pos,
                    "position_detail": pos_detail
                })
                
            except Exception as ex:
                decision_log.error(f"❌ [StockTradeRisk] 计算个股 [{code} ({name})] 交易风控参数异常: {ex}")
                all_missing.append(f"{code}({name}): 计价遭遇内部异常")
                
                # 保守风控硬性降级
                trade_list.append({
                    "stock_code": code,
                    "stock_name": name,
                    "entry_zone": "未知",
                    "support_price": "未知",
                    "pressure_price": "未知",
                    "stop_loss_price": "未知",
                    "trailing_rule": "出现程序计算异常，转为最保守人工观察风控",
                    "first_profit_price": "未知",
                    "second_profit_price": "未知",
                    "suggest_position": 0.02,
                    "position_detail": f"程序执行异常降级: {str(ex)}，强制降至最低仓位"
                })

        return trade_list, all_missing

    def run(
        self,
        score_result: Optional[Dict[str, Any]] = None,
        board_result: Optional[Dict[str, Any]] = None,
        macro_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        5. 统一入口。
        
        参数:
            score_result: 上层打分评级输出的字典
            board_result: 上层风格与收官输出的字典
            macro_mode: 第一层修正决定的宏观环境模式 ('进攻'/'谨慎'/'防守')
            
        返回:
            交易执行点位和风控建议。
        """
        decision_log.info("🚀 [StockTradeRisk] 启动交易风控点位与仓位计算流程...")
        
        # A. 前置链路阻断校验
        if score_result is None or score_result.get("flow_status") == "终止":
            decision_log.warning("⚠️ [StockTradeRisk] 前置评分阶段已被拦截阻断，不再生成交易点位参数。")
            return {
                "trade_list": [],
                "global_risk_tip": "交易流程已终止",
                "data_missing_list": [],
                "flow_status": "终止"
            }

        stock_score_list = score_result.get("stock_score_list", [])
        if not stock_score_list:
            decision_log.warning("⚠️ [StockTradeRisk] 候选被打分个股列表为空，无交易点位生成。")
            return {
                "trade_list": [],
                "global_risk_tip": "候选股为空，无需生成风控参数",
                "data_missing_list": [],
                "flow_status": "继续"
            }

        # B. 无参兼容补齐
        # 1. 补齐宏观模式
        if macro_mode is None:
            try:
                from decision_framework.macro_score import macro_score
                from decision_framework.macro_revise import macro_revise
                pre_score = macro_score.run()
                revise_res = macro_revise.run(pre_score, {
                    "up_down_ratio": 1.0,
                    "top_board_change": 2.0,
                    "limit_up_num": 15
                })
                macro_mode = revise_res.get("revised_mode", "谨慎")
            except Exception:
                macro_mode = "谨慎"

        # 2. 补齐大类风格和收官
        if board_result is None:
            try:
                board_result = board_link_siphon.run()
            except Exception:
                board_result = {"flow_status": "继续", "siphon_info": {}}

        # 提取风格热度
        style_group = []
        try:
            from decision_framework.board_rank import board_rank
            from decision_framework.board_style import board_style
            board_res = board_rank.run()
            style_res = board_style.run(board_res)
            style_group = style_res.get("style_group", [])
        except Exception as e:
            decision_log.warning(f"⚠️ [StockTradeRisk] 补齐大类板块风格热度出错: {e}")

        # 3. 补齐交易日
        latest_date = stock_filter.get_latest_trade_date()
        if not latest_date:
            decision_log.error("❌ [StockTradeRisk] 数据中心最新交易日为空，终止流程")
            return {
                "trade_list": [],
                "global_risk_tip": "交易日数据缺失导致终止",
                "data_missing_list": ["全局交易日历为空"],
                "flow_status": "终止"
            }

        # C. 批量风控与点位计算
        trade_list, missing_list = self.batch_calc(stock_score_list, style_group, macro_mode, latest_date)

        # D. 生成全局风险提示
        siphon_level = board_result.get("siphon_info", {}).get("siphon_level", "无虹吸")
        if macro_mode == "防守":
            global_risk_tip = (
                "【高风险警告】宏观模式判定为 [防守]，全市场环境转入收缩。止损幅度收窄至 3%，各标的已收窄止盈点位，"
                "仓位加权已自动调降，建议控制全市场总持仓在 30% 以下，谨慎防守。"
            )
        elif siphon_level in ["中等虹吸", "强虹吸"]:
            global_risk_tip = (
                f"【虹吸风险警报】检测到板块间存在 {siphon_level} 级别的资金虹吸效应。个股持仓应避免同一板块过度拥挤，"
                "优先关注安全区间内个股，执行严格移步锁定利润。"
            )
        else:
            global_risk_tip = (
                "【正常风控提示】市场整体处于相对安全的交易区间。个股仓位已根据打分、热度及宏观因子分配完毕，"
                "入场后请坚决服从各标的量化支撑止损与止盈纪律。"
            )

        result = {
            "trade_list": trade_list,
            "global_risk_tip": global_risk_tip,
            "data_missing_list": missing_list,
            "flow_status": "继续"
        }
        
        decision_log.info(
            f"✅ [StockTradeRisk] 个股点位及动态持仓配置完毕。生成标的数: {len(trade_list)} | "
            f"全局风控级别: {macro_mode} | 板块虹吸级别: {siphon_level}"
        )
        return result


# 对外提供全局单例对象
stock_trade_risk = StockTradeRisk()

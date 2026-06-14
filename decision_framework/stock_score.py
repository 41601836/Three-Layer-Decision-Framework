# -*- coding: utf-8 -*-
"""
stock_score.py —— 第三层个股执行决策框架：子模块2 个股多维度量化打分
==================================================================

本模块主要职责包括对初筛出的备选个股进行基本面、资金面、筹码结构三大维度共六个
细分指标的量化打分。评分梯度为 [1.0 / 0.5 / 0.0] 分：
1. 基本面评分：业绩同比增速（利润增长）、估值PE历史分位（近3年PE分布）；
2. 资金面评分：主力资金净流入占比、大单交易额占比；
3. 筹码面评分：股东户数集中度变动、近5日换手稳定性变异系数；
4. 综合加权评分与个股标签评级划分（优质/良好/一般/偏弱）；
5. 缺失数据优雅填充基准分（0.5分）与程序异常安全降级。
"""

import math
import sqlite3
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd

from decision_framework.stock_filter import stock_filter
from decision_framework.macro_query import macro_query
from config_loader import *
from decision_framework.decision_log import decision_log
from db.dao import dao


class StockScore:
    """
    个股基本面/资金面/筹码结构多维度量化打分单例类。
    """

    def calc_single_index(self, stock_code: str, index_name: str, latest_date: str) -> Tuple[float, str, bool]:
        """
        1. 单指标打分。
        
        入参:
            stock_code: 个股代码 (例如 000001.SZ)
            index_name: 指标名称 ('profit_yoy' / 'valuation_quantile' / 'net_inflow_ratio' / 'big_order_ratio' / 'chip_concentration' / 'turnover_stability')
            latest_date: 最新交易日
            
        返回:
            score: 指标得分 (1.0 / 0.5 / 0.0)
            reason: 判定说明
            is_missing: 是否属于数据缺失
        """
        conn = dao.get_conn()
        cursor = conn.cursor()
        
        try:
            # 1. 业绩增速打分
            if index_name == "profit_yoy":
                sql = """
                    SELECT profit_yoy FROM bak_basic 
                    WHERE ts_code = ? AND trade_date <= ? 
                    ORDER BY trade_date DESC LIMIT 1
                """
                cursor.execute(sql, (stock_code, latest_date))
                row = cursor.fetchone()
                if not row or row[0] is None:
                    return 0.5, "业绩增速数据缺失", True
                
                profit_yoy = float(row[0])
                yoy = profit_yoy / 100.0  # 数据库为百分比，转为小数
                
                if yoy >= PROFIT_HIGH:
                    return 1.0, f"高增长业绩(同比增速 {profit_yoy:.2f}%)", False
                elif yoy >= PROFIT_MID:
                    return 0.5, f"稳健业绩(同比增速 {profit_yoy:.2f}%)", False
                else:
                    return 0.0, f"低速或负增长业绩(同比增速 {profit_yoy:.2f}%)", False

            # 2. 估值分位打分
            elif index_name == "valuation_quantile":
                # 获取过去 3 年 (最多 750 个交易日) 的历史 PE
                sql = """
                    SELECT pe FROM daily_basic 
                    WHERE ts_code = ? AND trade_date <= ? 
                    ORDER BY trade_date DESC LIMIT 750
                """
                cursor.execute(sql, (stock_code, latest_date))
                rows = cursor.fetchall()
                if not rows or rows[0][0] is None:
                    return 0.5, "最新估值数据缺失", True
                
                latest_pe = float(rows[0][0])
                if latest_pe <= 0:
                    return 0.0, f"当前PE为负或零({latest_pe:.2f})，不参与低估评级", False
                
                # 过滤出有效的正数历史PE值
                pe_history = [float(r[0]) for r in rows if r[0] is not None and float(r[0]) > 0]
                if len(pe_history) < 20:
                    return 0.5, f"估值历史序列数据不足20日(实际 {len(pe_history)} 日)", True
                
                # 计算最新 PE 在历史中的分位数
                smaller_count = sum(1 for p in pe_history if p < latest_pe)
                quantile = smaller_count / len(pe_history)
                
                if quantile <= VAL_LOW:
                    return 1.0, f"估值低位分位(历史分位 {quantile:.2%})", False
                elif quantile <= VAL_MID:
                    return 0.5, f"估值合理分位(历史分位 {quantile:.2%})", False
                else:
                    return 0.0, f"估值高位分位(历史分位 {quantile:.2%})", False

            # 3. 主力净流入占比打分
            elif index_name == "net_inflow_ratio":
                sql = """
                    SELECT buy_sm_amount, sell_sm_amount, buy_md_amount, sell_md_amount,
                           buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount
                    FROM moneyflow 
                    WHERE ts_code = ? AND trade_date <= ? 
                    ORDER BY trade_date DESC LIMIT 1
                """
                cursor.execute(sql, (stock_code, latest_date))
                row = cursor.fetchone()
                if not row or None in row:
                    return 0.5, "主力流入资金明细数据缺失", True
                
                # 转换所有数据为浮点数
                sm_in, sm_out, md_in, md_out, lg_in, lg_out, elg_in, elg_out = [float(x) for x in row]
                
                # 主力净流入额 = (超大单买入 + 大单买入) - (超大单卖出 + 大单卖出)
                net_main = (elg_in + lg_in) - (elg_out + lg_out)
                # 总成交额近似为所有买入项之和
                total_flow = sm_in + md_in + lg_in + elg_in
                
                if total_flow <= 0:
                    return 0.0, "当日总买入额为0或负数", False
                
                net_ratio = net_main / total_flow
                
                if net_ratio >= NET_IN_HIGH:
                    return 1.0, f"主力资金显著吸金(占比 {net_ratio:.2%})", False
                elif net_ratio >= NET_IN_MID:
                    return 0.5, f"主力资金温和流入(占比 {net_ratio:.2%})", False
                else:
                    return 0.0, f"主力资金呈流出态势(占比 {net_ratio:.2%})", False

            # 4. 大单成交占比打分
            elif index_name == "big_order_ratio":
                sql = """
                    SELECT buy_sm_amount, sell_sm_amount, buy_md_amount, sell_md_amount,
                           buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount
                    FROM moneyflow 
                    WHERE ts_code = ? AND trade_date <= ? 
                    ORDER BY trade_date DESC LIMIT 1
                """
                cursor.execute(sql, (stock_code, latest_date))
                row = cursor.fetchone()
                if not row or None in row:
                    return 0.5, "大单交易明细数据缺失", True
                
                sm_in, sm_out, md_in, md_out, lg_in, lg_out, elg_in, elg_out = [float(x) for x in row]
                
                # 大单成交额（大单买+大单卖+超大买+超大卖）
                big_flow = lg_in + lg_out + elg_in + elg_out
                # 总成交买卖和
                total_flow_sum = (sm_in + sm_out + md_in + md_out + lg_in + lg_out + elg_in + elg_out)
                
                if total_flow_sum <= 0:
                    return 0.0, "当日总成交金额为0或负数", False
                
                big_ratio = big_flow / total_flow_sum
                
                if big_ratio >= BIG_ORDER_HIGH:
                    return 1.0, f"机构及主力大单主导(占比 {big_ratio:.2%})", False
                elif big_ratio >= BIG_ORDER_MID:
                    return 0.5, f"有主力资金参与(占比 {big_ratio:.2%})", False
                else:
                    return 0.0, f"散户小单主导(占比 {big_ratio:.2%})", False

            # 5. 筹码集中度打分
            elif index_name == "chip_concentration":
                # 查询最新两期股东户数记录
                sql = """
                    SELECT holder_num FROM stk_holdernumber 
                    WHERE ts_code = ? AND end_date <= ? 
                    ORDER BY end_date DESC LIMIT 2
                """
                cursor.execute(sql, (stock_code, latest_date))
                rows = cursor.fetchall()
                if not rows or rows[0][0] is None:
                    return 0.5, "最新两期股东户数缺失", True
                
                holder_curr = int(rows[0][0])
                if len(rows) < 2:
                    # 仅有一期股东户数数据，无法计算变动率，降级填充中性分 0.5 (非缺失)
                    return 0.5, "仅有单期股东户数，无法对比趋势", False
                
                holder_prev = int(rows[1][0])
                if holder_prev <= 0:
                    return 0.5, "上期股东户数为零或负数，无法对比", False
                
                # 计算股东户数变动率，负值表示股东户数减少，筹码越集中
                holder_chg = (holder_curr - holder_prev) / holder_prev
                
                if holder_chg <= -0.05:
                    return 1.0, f"筹码高度集中(股东户数较上期减少 {abs(holder_chg):.2%})", False
                elif holder_chg < 0:
                    return 0.5, f"筹码温和集中(股东户数较上期减少 {abs(holder_chg):.2%})", False
                else:
                    return 0.0, f"筹码发散(股东户数较上期增加 {holder_chg:.2%})", False

            # 6. 换手稳定性打分
            elif index_name == "turnover_stability":
                # 查询最新 5 日换手率
                sql = """
                    SELECT turnover_rate FROM daily_basic 
                    WHERE ts_code = ? AND trade_date <= ? 
                    ORDER BY trade_date DESC LIMIT 5
                """
                cursor.execute(sql, (stock_code, latest_date))
                rows = cursor.fetchall()
                if not rows or len(rows) < 5:
                    return 0.5, "近5日换手率历史不足5日", True
                
                rates = []
                for r in rows:
                    if r[0] is None:
                        return 0.5, "近5日换手率包含空值", True
                    rates.append(float(r[0]))
                
                mean_val = sum(rates) / len(rates)
                if mean_val <= 0:
                    return 0.0, "近5日平均换手率为0，波动过大", False
                
                # 计算标准差
                variance = sum((x - mean_val) ** 2 for x in rates) / len(rates)
                std_val = math.sqrt(variance)
                
                # 变异系数 (标准差 / 平均值)
                cv_val = std_val / mean_val
                
                if cv_val < TURNOVER_STABLE:
                    return 1.0, f"换手极其平稳(近5日换手标准差/均值 {cv_val:.2%})", False
                elif cv_val < TURNOVER_MID:
                    return 0.5, f"换手基本稳定(近5日换手标准差/均值 {cv_val:.2%})", False
                else:
                    return 0.0, f"换手异常波动(近5日换手标准差/均值 {cv_val:.2%})", False

            else:
                return 0.5, f"未知的打分指标: {index_name}", True

        except Exception as e:
            decision_log.error(f"❌ [StockScore] 计算指标 [{index_name}] 异常: {e} | 个股: {stock_code}")
            return 0.5, f"计算指标出错: {str(e)}", True
        finally:
            conn.close()

    def calc_dimension_score(self, stock_code: str, latest_date: str) -> Tuple[float, float, float, List[str], List[str]]:
        """
        2. 三大维度汇总得分。
        
        功能:
            调用单指标评分方法，分别汇总计算基本面、资金面、筹码结构三大维度得分。
            单指标数据缺失填充基准分 (0.5分)，并添加至缺失清单。
            
        返回:
            fundamental_score: 基本面平均分
            capital_score: 资金面平均分
            chip_score: 筹码结构平均分
            details: 包含六大单指标得分及判定描述的字符串列表
            missing_list: 该个股明细数据缺失清单
        """
        details = []
        missing_list = []
        
        # A. 基本面维度打分
        s_yoy, r_yoy, m_yoy = self.calc_single_index(stock_code, "profit_yoy", latest_date)
        s_val, r_val, m_val = self.calc_single_index(stock_code, "valuation_quantile", latest_date)
        
        fund_score = (s_yoy + s_val) / 2.0
        details.append(f"基本面-业绩增速: {s_yoy:.1f}分 ({r_yoy})")
        details.append(f"基本面-估值分位: {s_val:.1f}分 ({r_val})")
        
        if m_yoy: missing_list.append(f"业绩同比增速")
        if m_val: missing_list.append(f"估值PE历史序列")
        
        # B. 资金面维度打分
        s_net, r_net, m_net = self.calc_single_index(stock_code, "net_inflow_ratio", latest_date)
        s_big, r_big, m_big = self.calc_single_index(stock_code, "big_order_ratio", latest_date)
        
        cap_score = (s_net + s_big) / 2.0
        details.append(f"资金面-流入占比: {s_net:.1f}分 ({r_net})")
        details.append(f"资金面-大单占比: {s_big:.1f}分 ({r_big})")
        
        if m_net: missing_list.append(f"主力净流入数据")
        if m_big: missing_list.append(f"大单买卖明细")
        
        # C. 筹码结构维度打分
        s_conc, r_conc, m_conc = self.calc_single_index(stock_code, "chip_concentration", latest_date)
        s_stab, r_stab, m_stab = self.calc_single_index(stock_code, "turnover_stability", latest_date)
        
        chip_score = (s_conc + s_stab) / 2.0
        details.append(f"筹码面-筹码集中: {s_conc:.1f}分 ({r_conc})")
        details.append(f"筹码面-换手稳定: {s_stab:.1f}分 ({r_stab})")
        
        if m_conc: missing_list.append(f"股东户数记录")
        if m_stab: missing_list.append(f"5日换手历史")
        
        return fund_score, cap_score, chip_score, details, missing_list

    def calc_total_score(self, fund_score: float, cap_score: float, chip_score: float) -> Tuple[float, str]:
        """
        3. 加权计算综合总分与个股评级。
        
        加权公式:
            总分 = 基础面 * 0.40 + 资金面 * 0.35 + 筹码结构 * 0.25
            
        评级标准:
            总分 >= 0.8  => 优质标的
            0.6 <= 总分 < 0.8 => 良好标的
            0.4 <= 总分 < 0.6 => 一般标的
            总分 < 0.4  => 偏弱标的
        """
        total_score = (
            fund_score * WEIGHT_FUNDAMENTAL +
            cap_score * WEIGHT_CAPITAL +
            chip_score * WEIGHT_CHIP
        )
        # 截断四舍五入保留4位小数以防精度浮点抖动
        total_score = round(total_score, 4)
        
        if total_score >= SCORE_EXCELLENT:
            level = "优质标的"
        elif total_score >= SCORE_GOOD:
            level = "良好标的"
        elif total_score >= SCORE_NORMAL:
            level = "一般标的"
        else:
            level = "偏弱标的"
            
        return total_score, level

    def batch_score(self, stock_list: List[Dict[str, Any]], latest_date: str) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
        """
        4. 批量对备选个股执行打分流。
        
        功能:
            串行计算每个个股的指标，并在遇到数据库或解析异常时安全捕获，
            记录异常状态并将其评级下调。
            
        返回:
            scored_stocks: 成功打分的个股明细列表
            all_missing: 汇集的缺失指标说明
            abnormal_stocks: 异常个股说明列表
        """
        scored_stocks = []
        all_missing = []
        abnormal_stocks = []

        for stk in stock_list:
            code = stk["stock_code"]
            name = stk["stock_name"]
            
            try:
                # 维度打分
                fund_score, cap_score, chip_score, details, missing_list = self.calc_dimension_score(code, latest_date)
                
                # 综合计算
                total_score, level = self.calc_total_score(fund_score, cap_score, chip_score)
                
                # 归集数据缺失项
                if missing_list:
                    all_missing.append(f"{code}({name}) 缺失指标: {', '.join(missing_list)}")
                    
                scored_stocks.append({
                    "stock_code": code,
                    "stock_name": name,
                    "fundamental_score": round(fund_score, 4),
                    "capital_score": round(cap_score, 4),
                    "chip_score": round(chip_score, 4),
                    "total_score": total_score,
                    "stock_level": level,
                    "detail": details
                })
                
                decision_log.info(f"📊 [StockScore] 个股 [{code} ({name})] 打分完毕: 综合分 {total_score:.2f} | 评级 {level}")
                
            except Exception as e:
                decision_log.error(f"❌ [StockScore] 打分个股 [{code} ({name})] 遭遇系统异常: {e}")
                abnormal_stocks.append(f"{code}({name}): 系统执行出错: {str(e)}")
                
                # 异常降级处理
                scored_stocks.append({
                    "stock_code": code,
                    "stock_name": name,
                    "fundamental_score": 0.0,
                    "capital_score": 0.0,
                    "chip_score": 0.0,
                    "total_score": 0.0,
                    "stock_level": "偏弱标的",
                    "detail": [f"打分计算遇到未知内部错误: {str(e)}，安全降级评为偏弱"]
                })

        return scored_stocks, all_missing, abnormal_stocks

    def run(self, filter_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        5. 统一对外入口。
        
        参数:
            filter_result: 前置 stock_filter 输出的结果字典
            
        返回:
            量化评分标准结果字典。
        """
        decision_log.info("🚀 [StockScore] 启动个股多维度量化打分引擎...")
        
        # A. 校验上层决策流向状态，若已被拦截则直接阻断
        if filter_result is None or filter_result.get("flow_status") == "终止":
            decision_log.warning("⚠️ [StockScore] 上游初筛模块输出为 [终止]，打分流程直接终止。")
            return {
                "stock_score_list": [],
                "data_missing_list": [],
                "abnormal_stock_list": [],
                "flow_status": "终止"
            }

        # B. 提取备选个股列表
        candidate_stocks = filter_result.get("candidate_stocks", [])
        if not candidate_stocks:
            decision_log.warning("⚠️ [StockScore] 未检测到任何备选候选个股，打分中止。")
            return {
                "stock_score_list": [],
                "data_missing_list": [],
                "abnormal_stock_list": [],
                "flow_status": "继续"
            }

        latest_date = stock_filter.get_latest_trade_date()
        if not latest_date:
            decision_log.error("❌ [StockScore] 数据库中没有可用的交易日行情，终止流程")
            return {
                "stock_score_list": [],
                "data_missing_list": ["全局交易日历为空"],
                "abnormal_stock_list": [],
                "flow_status": "终止"
            }

        # C. 批量打分
        scored_list, missing_list, abnormal_list = self.batch_score(candidate_stocks, latest_date)
        
        result = {
            "stock_score_list": scored_list,
            "data_missing_list": missing_list,
            "abnormal_stock_list": abnormal_list,
            "flow_status": "继续"
        }
        
        decision_log.info(
            f"✅ [StockScore] 个股多维度打分完毕。打分数量: {len(scored_list)} | "
            f"异常数: {len(abnormal_list)} | 包含缺失个股数: {len(missing_list)}"
        )
        return result


# 对外提供全局单例对象
stock_score = StockScore()

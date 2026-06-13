# -*- coding: utf-8 -*-
"""
macro_score.py —— 第一层宏观环境诊断综合打分与决策引擎
=====================================================

基于 macro_query 获取的宏观数据，严格按照业务文档评估六大维度指标，判定红绿黄灯态。
最终根据综合加权总分、强制一票否决规则输出操作模式及仓位上限。
"""

import pandas as pd
import json
from typing import Dict, List, Any

from decision_framework.macro_query import macro_query
from config_loader import *
from decision_framework.decision_log import decision_log


class MacroScore:
    """
    第一层宏观诊断综合打分卡。
    提供统一入口 run() 进行评估。
    """

    def _score_global_macro(self, missing_list: List[str]) -> Dict[str, Any]:
        """
        维度1：全球宏观与资金风险偏好打分
        """
        try:
            data = macro_query.get_global_macro()
            if data.get("data_missing", True):
                missing_list.append("全球宏观(global_macro_daily)")
                return {
                    "name": "全球宏观与资金风险偏好",
                    "score": DIM_SCORE_YELLOW,
                    "light": "yellow",
                    "reason": "【数据缺失】全球宏观表数据缺失，降级为黄灯中性评估",
                    "weight": 1.0
                }

            vix = data.get("vix")
            spx_pct = data.get("spx_pct")
            brent_pct = data.get("brent_pct")
            dxy = data.get("dxy")

            # 异常值校验
            if vix is None or spx_pct is None:
                missing_list.append("全球宏观字段(vix/spx_pct)")
                return {
                    "name": "全球宏观与资金风险偏好",
                    "score": DIM_SCORE_YELLOW,
                    "light": "yellow",
                    "reason": "【数据缺失】全球宏观关键字段缺失，降级为黄灯评估",
                    "weight": 1.0
                }

            # 判定逻辑
            # 1. 系统性冲击 (红灯)
            if vix > 30.0 or spx_pct <= -2.5 or (brent_pct is not None and brent_pct <= -5.0) or (dxy is not None and dxy > 105.0):
                reason = f"系统性冲击: VIX波动率({vix})或外盘指数严重下跌"
                return {
                    "name": "全球宏观与资金风险偏好",
                    "score": DIM_SCORE_RED,
                    "light": "red",
                    "reason": reason,
                    "weight": 1.0
                }
            # 2. 局部波动 (黄灯)
            elif vix > 20.0 or spx_pct <= -1.0 or (dxy is not None and dxy > 102.5):
                reason = f"局部波动: VIX波动率上升({vix})或外盘指数震荡"
                return {
                    "name": "全球宏观与资金风险偏好",
                    "score": DIM_SCORE_YELLOW,
                    "light": "yellow",
                    "reason": reason,
                    "weight": 1.0
                }
            # 3. 稳定平稳 (绿灯)
            else:
                return {
                    "name": "全球宏观与资金风险偏好",
                    "score": DIM_SCORE_GREEN,
                    "light": "green",
                    "reason": f"外围整体平稳: VIX({vix}), 汇率及外盘运行健康",
                    "weight": 1.0
                }
        except Exception as e:
            decision_log.error(f"❌ [MacroScore] 维度1评估异常: {e}")
            return {
                "name": "全球宏观与资金风险偏好",
                "score": DIM_SCORE_YELLOW,
                "light": "yellow",
                "reason": f"评估异常: {str(e)}，降级为黄灯",
                "weight": 1.0
            }

    def _score_policy_environment(self, missing_list: List[str]) -> Dict[str, Any]:
        """
        维度2：国内政策环境与流动性打分 (降级处理)
        """
        # 国内政策环境目前无专门表存储，按缺失容错逻辑置为黄灯评估
        missing_list.append("国内政策与流动性数据")
        return {
            "name": "国内政策环境与流动性",
            "score": DIM_SCORE_YELLOW,
            "light": "yellow",
            "reason": "【数据缺失】国内政策数据缺失，降级为黄灯中性评估",
            "weight": 1.0
        }

    def _score_macro_economy(self, missing_list: List[str]) -> Dict[str, Any]:
        """
        维度3：宏观经济基本面打分
        """
        try:
            data = macro_query.get_macro_econ()
            if data.get("data_missing", True):
                missing_list.append("国内宏观经济(china_macro_indicators)")
                return {
                    "name": "宏观经济基本面",
                    "score": DIM_SCORE_YELLOW,
                    "light": "yellow",
                    "reason": "【数据缺失】国内经济数据缺失，降级为黄灯中性评估",
                    "weight": 1.0
                }

            pmi_man = data.get("pmi_man")
            cpi = data.get("cpi")
            gdp_growth = data.get("gdp_growth")

            if pmi_man is None:
                missing_list.append("经济基本面字段(pmi_man)")
                return {
                    "name": "宏观经济基本面",
                    "score": DIM_SCORE_YELLOW,
                    "light": "yellow",
                    "reason": "【数据缺失】制造业 PMI 指标缺失，降级为黄灯评估",
                    "weight": 1.0
                }

            # 判定逻辑 (PMI 主导)
            # 1. 经济向好 (绿灯)
            if pmi_man >= 50.0:
                reason = f"经济基本面向好: 制造业PMI位于荣枯线以上({pmi_man})"
                return {
                    "name": "宏观经济基本面",
                    "score": DIM_SCORE_GREEN,
                    "light": "green",
                    "reason": reason,
                    "weight": 1.0
                }
            # 2. 走弱 (红灯)
            elif pmi_man < 48.5:
                reason = f"经济下行明显: 制造业PMI严重萎缩({pmi_man})"
                return {
                    "name": "宏观经济基本面",
                    "score": DIM_SCORE_RED,
                    "light": "red",
                    "reason": reason,
                    "weight": 1.0
                }
            # 3. 震荡收缩 (黄灯)
            else:
                reason = f"区间震荡收缩: 制造业PMI位于临界区({pmi_man})"
                return {
                    "name": "宏观经济基本面",
                    "score": DIM_SCORE_YELLOW,
                    "light": "yellow",
                    "reason": reason,
                    "weight": 1.0
                }
        except Exception as e:
            decision_log.error(f"❌ [MacroScore] 维度3评估异常: {e}")
            return {
                "name": "宏观经济基本面",
                "score": DIM_SCORE_YELLOW,
                "light": "yellow",
                "reason": f"评估异常: {str(e)}，降级为黄灯",
                "weight": 1.0
            }

    def _score_market_capital(self, missing_list: List[str]) -> Dict[str, Any]:
        """
        维度4：市场资金结构打分 (含动态权重判断)
        """
        try:
            data = macro_query.get_market_capital()
            if data.get("data_missing", True):
                missing_list.append("市场资金数据(daily_prices/hsgt_moneyflow)")
                return {
                    "name": "市场资金结构",
                    "score": DIM_SCORE_YELLOW,
                    "light": "yellow",
                    "reason": "【数据缺失】市场交易与流入额缺失，降级为黄灯评估",
                    "weight": WEIGHT_CAPITAL_NORMAL
                }

            total_amount = data.get("total_amount")
            north_money = data.get("north_money")

            if total_amount is None:
                missing_list.append("资金结构核心字段(total_amount)")
                return {
                    "name": "市场资金结构",
                    "score": DIM_SCORE_YELLOW,
                    "light": "yellow",
                    "reason": "【数据缺失】两市成交总额缺失，降级为黄灯评估",
                    "weight": WEIGHT_CAPITAL_NORMAL
                }

            # 1. 动态权重判断
            weight = WEIGHT_CAPITAL_HIGH if total_amount > CAPITAL_DYNAMIC_THRESHOLD else WEIGHT_CAPITAL_NORMAL

            # 2. 基础打分判定
            # 资金充裕 (绿灯)
            if total_amount >= 1000000000 or (north_money is not None and north_money > 5000): # 10亿或北向大流入
                reason = f"资金充裕: 两市成交额为 {total_amount / 1e8:.2f} 亿，北向资金流入为 {north_money}"
                base_score = DIM_SCORE_GREEN
                light = "green"
            # 资金一般 (黄灯)
            elif total_amount >= 500000000:
                reason = f"资金中性: 两市成交额为 {total_amount / 1e8:.2f} 亿"
                base_score = DIM_SCORE_YELLOW
                light = "yellow"
            # 资金流失 (红灯)
            else:
                reason = f"资金流失: 两市成交总额为 {total_amount / 1e8:.2f} 亿，成交量低迷"
                base_score = DIM_SCORE_RED
                light = "red"

            # 3. 乘以权重算出真实得分 (最终得分 = 基础分 * 权重)
            score = base_score * weight

            return {
                "name": "市场资金结构",
                "score": score,
                "light": light,
                "reason": f"{reason} | 动态权重: {weight}",
                "weight": weight
            }
        except Exception as e:
            decision_log.error(f"❌ [MacroScore] 维度4评估异常: {e}")
            return {
                "name": "市场资金结构",
                "score": DIM_SCORE_YELLOW,
                "light": "yellow",
                "reason": f"评估异常: {str(e)}，降级为黄灯",
                "weight": WEIGHT_CAPITAL_NORMAL
            }

    def _score_market_sentiment(self, missing_list: List[str]) -> Dict[str, Any]:
        """
        维度5：市场情绪温度打分
        """
        try:
            data = macro_query.get_market_sentiment()
            if data.get("data_missing", True):
                # 即使缺失，如果有 up/down 也可以尝试不完全降级
                up_count = data.get("up_count")
                down_count = data.get("down_count")
                if up_count is None or down_count is None:
                    missing_list.append("市场情绪数据")
                    return {
                        "name": "市场情绪温度",
                        "score": DIM_SCORE_YELLOW,
                        "light": "yellow",
                        "reason": "【数据缺失】情绪统计完全缺失，降级为黄灯评估",
                        "weight": 1.0
                    }

            up_count = data.get("up_count")
            down_count = data.get("down_count")
            limit_up = data.get("limit_up")
            limit_down = data.get("limit_down")

            # 判定逻辑 (涨跌家数比)
            total_count = up_count + down_count
            if total_count == 0:
                missing_list.append("上涨下跌零计数")
                return {
                    "name": "市场情绪温度",
                    "score": DIM_SCORE_YELLOW,
                    "light": "yellow",
                    "reason": "【数据缺失】有效股票交易计数为零，降级为黄灯评估",
                    "weight": 1.0
                }

            up_ratio = up_count / total_count

            # 1. 情绪活跃 (绿灯)
            if up_ratio >= 0.60 or (limit_up is not None and limit_up > 80):
                reason = f"情绪活跃: 上涨家数占 {up_ratio:.1%}，涨停家数: {limit_up}"
                return {
                    "name": "市场情绪温度",
                    "score": DIM_SCORE_GREEN,
                    "light": "green",
                    "reason": reason,
                    "weight": 1.0
                }
            # 2. 情绪低迷/崩塌 (红灯)
            elif up_ratio < 0.35 or (limit_down is not None and limit_down > 50):
                reason = f"情绪低迷崩溃: 下跌家数明显占优，跌停家数: {limit_down}"
                return {
                    "name": "市场情绪温度",
                    "score": DIM_SCORE_RED,
                    "light": "red",
                    "reason": reason,
                    "weight": 1.0
                }
            # 3. 中性 (黄灯)
            else:
                reason = f"情绪中性: 上涨家数占比为 {up_ratio:.1%}"
                return {
                    "name": "市场情绪温度",
                    "score": DIM_SCORE_YELLOW,
                    "light": "yellow",
                    "reason": reason,
                    "weight": 1.0
                }
        except Exception as e:
            decision_log.error(f"❌ [MacroScore] 维度5评估异常: {e}")
            return {
                "name": "市场情绪温度",
                "score": DIM_SCORE_YELLOW,
                "light": "yellow",
                "reason": f"评估异常: {str(e)}，降级为黄灯",
                "weight": 1.0
            }

    def _score_institutional_consensus(self, missing_list: List[str]) -> Dict[str, Any]:
        """
        维度6：机构共识校准打分 (降级处理)
        """
        # 券商研报等暂无对应表，默认降级评估为黄灯
        missing_list.append("机构研报与共识数据")
        return {
            "name": "机构共识校准",
            "score": DIM_SCORE_YELLOW,
            "light": "yellow",
            "reason": "【数据缺失】机构研究共识缺失，降级为黄灯中性评估",
            "weight": 1.0
        }

    # =========================================================================
    # 综合结果计算与模式判定
    # =========================================================================

    def run(self) -> Dict[str, Any]:
        """
        综合打分判定统一入口。
        """
        decision_log.info("🚀 [MacroScore] 开始执行宏观环境诊断打分流程...")
        missing_list = []

        # 1. 收集六大维度明细
        dims = [
            self._score_global_macro(missing_list),
            self._score_policy_environment(missing_list),
            self._score_macro_economy(missing_list),
            self._score_market_capital(missing_list),
            self._score_market_sentiment(missing_list),
            self._score_institutional_consensus(missing_list)
        ]

        # 2. 检查是否有任一维度被判定为红灯 (一票否决规则)
        has_red = False
        red_dim_name = ""
        for dim in dims:
            if dim["light"] == "red":
                has_red = True
                red_dim_name = dim["name"]
                break

        # 3. 计算加权后综合总分
        # 公式：总分 = (所有维度加权得分之和 / 权重之和) * 6 (以缩放到 0 ~ 6 分区间与阈值对比)
        sum_weight_score = sum(dim["score"] for dim in dims) # 基础分已经乘过权重了
        sum_weight = sum(dim["weight"] for dim in dims)
        total_score = (sum_weight_score / sum_weight) * 6.0

        # 4. 根据总分区间与否决逻辑判定模式及仓位
        if has_red:
            operate_mode = "防守"
            position_limit = POS_DEFEND
            flow_status = "终止"
            decision_log.warning(
                f"🚨 [MacroScore] 触发强制一票否决! 维度 [{red_dim_name}] 判定为红灯态(0.0分)。"
                f"系统强制锁定防守模式，决策流程终止。综合加权计算分数为: {total_score:.2f}。"
            )
        else:
            if total_score >= SCORE_ATTACK:
                operate_mode = "进攻"
                position_limit = POS_ATTACK
                flow_status = "继续"
            elif total_score >= SCORE_CAUTIOUS_LOW:
                operate_mode = "谨慎"
                position_limit = POS_CAUTIOUS
                flow_status = "继续"
            else:
                operate_mode = "防守"
                position_limit = POS_DEFEND
                flow_status = "终止"

            decision_log.info(
                f"📊 [MacroScore] 评分完成。加权综合总分: {total_score:.2f}分，"
                f"判定模式: [{operate_mode}]，仓位上限: {position_limit:.0%}，决策流向: [{flow_status}]。"
            )

        # 组装对外标准字典
        result = {
            "dimensions": [
                {
                    "name": dim["name"],
                    "score": dim["score"],
                    "light": dim["light"],
                    "reason": dim["reason"]
                }
                for dim in dims
            ],
            "total_score": round(total_score, 2),
            "operate_mode": operate_mode,
            "position_limit": position_limit,
            "data_missing_list": missing_list,
            "flow_status": flow_status
        }
        return result


# 对外提供全局打分单例
macro_score = MacroScore()

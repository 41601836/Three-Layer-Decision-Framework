# -*- coding: utf-8 -*-
"""
board_position.py —— 第二层板块风格与轮动决策框架：大类板块合并仓位管控子模块
====================================================================

本模块作为交易落地的核心风控防御层，承接上层风格划分与轮动强度的评定结果，独立实现：
1. 大类风格合并仓位计算：将同属科技、消费、大金融、周期、赛道的板块仓位进行归集；
2. 静态仓位上限风控分级：对比配置阈值，判定仓位状态（正常、预警、强制减仓）；
3. 动态上限修正：根据风格大类的强弱热度及市场整体的轮动强度联动对上限执行上浮或下调；
4. 综合风控提示与流程阻断：对全市场或风格大类的超仓触发进行流程终止阻断。
"""

import os
import json
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd

from decision_framework.board_style import board_style
from decision_framework.board_rotation import board_rotation
from config_loader import *
from decision_framework.decision_log import decision_log


class BoardPosition:
    """
    大类风格合并仓位管控与动态风控评级单例类。
    """

    def calc_merge_position(
        self, style_group: List[Dict[str, Any]], board_positions: Dict[str, float]
    ) -> Tuple[Dict[str, float], float]:
        """
        1. 大类风格合并仓位计算

        按大科技、大消费、大金融、周期、赛道五大主流大类合并统计其包含板块的仓位总和。
        同时，计算全市场所有板块的总持仓仓位。
        """
        target_styles = ["科技", "消费", "大金融", "周期", "赛道"]
        style_merge_pos = {s: 0.0 for s in target_styles}

        # 遍历归类好的风格分组，汇总各风格名下所有板块的实际持仓
        for item in style_group:
            s_name = item.get("style_name")
            if not s_name or s_name not in target_styles:
                continue
            b_list = item.get("board_list", [])
            m_pos = 0.0
            for board in b_list:
                m_pos += board_positions.get(board, 0.0)
            style_merge_pos[s_name] = m_pos

        # 全市场综合仓位：统计传入的板块持仓的总和
        market_total_pos = sum(board_positions.values())

        return style_merge_pos, market_total_pos

    def judge_position_status(self, pos: float, max_pos: float) -> str:
        """
        2. 静态仓位风控状态判定

        静态阈值等级标准：
        - pos < max_pos * WARN_RATIO           => 正常
        - max_pos * WARN_RATIO <= pos < max_pos => 预警
        - pos >= max_pos                        => 强制减仓
        """
        try:
            val_pos = float(pos)
            val_max = float(max_pos)
            warn_line = val_max * WARN_RATIO

            if val_pos < warn_line:
                return "正常"
            elif warn_line <= val_pos < val_max:
                return "预警"
            else:
                return "强制减仓"
        except Exception as e:
            decision_log.error(f"❌ [BoardPosition] 判定仓位状态出现类型转换异常: {e}")
            # 异常发生时，遵循高风控保守原则，默认返回强制减仓
            return "强制减仓"

    def dynamic_adjust(self, style_item: Dict[str, Any], rotate_strength: str) -> float:
        """
        3. 动态仓位上限修正

        结合大类风格在日内/跨日热度的强弱表现，以及全市场整体的轮动强度进行联动调整：
        - 上浮 (STRONG_COEFF): 风格日内为“强势”或跨日为“强势”，或者市场为“强轮动”；
        - 下调 (WEAK_COEFF): 风格日内为“弱势”且跨日为“弱势”，或者市场为“弱轮动”；
        - 保持不变: 处于其他中性温和状态。
        """
        intra_st = style_item.get("intraday_strength")
        cross_st = style_item.get("cross_day_strength")

        # 判断是否满足上浮（强势风格或强轮动）
        is_strong = (intra_st == "强势" or cross_st == "强势" or rotate_strength == "强轮动")

        # 判断是否满足下调（弱势风格或弱轮动）
        is_weak = False
        if not is_strong:
            is_weak = (intra_st == "弱势" or cross_st == "弱势" or rotate_strength == "弱轮动")

        # 根据强弱评级调整上限
        if is_strong:
            dynamic_max = STYLE_MAX_POS * STRONG_COEFF
            decision_log.info(f"📈 [BoardPosition] 风格 [{style_item.get('style_name')}] 动态上限上浮修正：{STYLE_MAX_POS:.2%} -> {dynamic_max:.2%}")
        elif is_weak:
            dynamic_max = STYLE_MAX_POS * WEAK_COEFF
            decision_log.info(f"📉 [BoardPosition] 风格 [{style_item.get('style_name')}] 动态上限下调修正：{STYLE_MAX_POS:.2%} -> {dynamic_max:.2%}")
        else:
            dynamic_max = STYLE_MAX_POS

        return dynamic_max

    def run(
        self,
        style_result: Dict[str, Any],
        rotation_result: Dict[str, Any],
        board_positions: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        大类板块合并仓位管控及风控决策主入口。
        """
        decision_log.info("🚀 [BoardPosition] 开始执行大类板块合并仓位管控分析...")
        data_missing_list = []
        flow_status = "继续"

        # 1. 容错：如果未传仓位，尝试读取本地 json。若无法读取则降级
        if board_positions is None:
            portfolio_path = "portfolio.json"
            if os.path.exists(portfolio_path):
                try:
                    with open(portfolio_path, "r", encoding="utf-8") as f:
                         content = f.read().strip()
                         if content:
                             data = json.loads(content)
                             if isinstance(data, dict):
                                 board_positions = {k: float(v) for k, v in data.items()}
                             elif isinstance(data, list):
                                 board_positions = {}
                                 for item in data:
                                     if isinstance(item, dict) and "board_name" in item:
                                         b_name = item["board_name"]
                                         b_pos = item.get("position", item.get("pos", 0.0))
                                         board_positions[b_name] = float(b_pos)
                             else:
                                 raise ValueError("不支持的资产配置格式")
                         else:
                             board_positions = {}
                except Exception as e:
                    decision_log.warning(f"⚠️ [BoardPosition] 读取本地 portfolio.json 异常: {e}，默认以空仓位运行")
                    board_positions = {}
                    data_missing_list.append("板块持仓数据(文件解析异常)")
            else:
                board_positions = {}
                data_missing_list.append("板块持仓数据(文件不存在)")

        try:
            style_group = style_result.get("style_group", [])
            rotate_strength = rotation_result.get("rotate_strength", "弱轮动")

            # 2. 合并仓位计算
            style_merge_pos, market_total_pos = self.calc_merge_position(style_group, board_positions)

            # 3. 各大类风格风控判定与动态调整
            style_pos_output = []
            has_forced_reduce = False

            # 我们只输出核心的 5 个主流大类风格，如果上一层含有“未知”，我们也在合并中处理
            target_styles = ["科技", "消费", "大金融", "周期", "赛道"]
            
            for s_name in target_styles:
                # 寻找上一层的风格明细以获取其热度强弱
                match_items = [item for item in style_group if item.get("style_name") == s_name]
                # 若上一层中没有候选板块涉及此风格，我们虚构一个中性风格项用于判定
                style_item = match_items[0] if match_items else {
                    "style_name": s_name,
                    "board_list": [],
                    "intraday_strength": "数据不足",
                    "cross_day_strength": "数据不足"
                }

                merge_pos = style_merge_pos.get(s_name, 0.0)

                # 计算动态仓位上限
                dynamic_max = self.dynamic_adjust(style_item, rotate_strength)

                # 基于静态上限进行仓位等级评估
                static_status = self.judge_position_status(merge_pos, STYLE_MAX_POS)

                # 生成建议持仓区间
                if static_status == "强制减仓":
                    suggest_pos = "超仓，请强制减仓！"
                    has_forced_reduce = True
                elif static_status == "预警":
                    suggest_pos = f"预警，建议减仓至 {STYLE_MAX_POS * WARN_RATIO:.1%} 以下"
                else:
                    suggest_pos = f"0.0% - {dynamic_max:.1%}"

                style_pos_output.append({
                    "style_name": s_name,
                    "merge_pos": round(merge_pos, 4),
                    "static_status": static_status,
                    "dynamic_max": round(dynamic_max, 4),
                    "suggest_pos": suggest_pos
                })

            # 4. 全市场仓位状态与动态调整
            # 同样地，全市场也结合轮动强度做动态上限微调（若为强轮动上调，若为弱轮动下调，否则保持）
            if rotate_strength == "强轮动":
                market_dynamic_max = MARKET_MAX_POS * STRONG_COEFF
            elif rotate_strength == "弱轮动":
                market_dynamic_max = MARKET_MAX_POS * WEAK_COEFF
            else:
                market_dynamic_max = MARKET_MAX_POS

            market_status = self.judge_position_status(market_total_pos, MARKET_MAX_POS)
            if market_status == "强制减仓":
                has_forced_reduce = True

            # 5. 风控提示与流程流向决策
            if has_forced_reduce:
                risk_notice = "【风控警报】当前已有风格板块或全市场总持仓超上限，请执行强制减仓风控指令！"
                flow_status = "终止"
                decision_log.warning(f"⚠️ [BoardPosition] 触发高风控拦截，流程终止！全市场仓位: {market_total_pos:.2%}")
            elif market_status == "预警" or any(x["static_status"] == "预警" for x in style_pos_output):
                risk_notice = "【风控提示】当前部分风格板块或全市场仓位已触发预警线，建议逐步减仓，控制风险。"
                decision_log.warning("⚠️ [BoardPosition] 部分持仓仓位触发预警线")
            else:
                risk_notice = "【风控正常】当前各风格大类及全市场仓位控制良好，处于安全区间。"
                decision_log.info("ℹ️ [BoardPosition] 持仓仓位风控状态正常")

            result = {
                "style_position": style_pos_output,
                "market_total_pos": round(market_total_pos, 4),
                "market_status": market_status,
                "risk_notice": risk_notice,
                "data_missing_list": list(set(data_missing_list)),
                "flow_status": flow_status
            }

            decision_log.info("✅ [BoardPosition] 仓位管控与风控状态评估完毕。")
            return result

        except Exception as e:
            decision_log.error(f"❌ [BoardPosition] 统一运行入口捕获未处理异常: {e}")
            # 高风控原则：默认返回终止和强制减仓状态
            return {
                "style_position": [],
                "market_total_pos": 0.0,
                "market_status": "强制减仓",
                "risk_notice": f"【系统风控错误】: {str(e)}",
                "data_missing_list": ["系统运行异常"],
                "flow_status": "终止"
            }


# 对外提供全局单例对象
board_position = BoardPosition()

# -*- coding: utf-8 -*-
"""
workflow_total.py —— 第三层决策框架：顶层全流程整合总调度工作流
============================================================

本模块作为整套量化决策系统的唯一顶层总入口，负责串联以下层级和子模块：
1. 第一层 宏观环境诊断：打分 (macro_score) -> 否决 (macro_veto) -> 盘中修正 (macro_revise)
2. 第二层 板块风格轮动：板块筛选 (board_rank) -> 结构判定 (board_structure) -> 风格热度 (board_style)
   -> 轮动信号 (board_rotation) -> 仓位管控 (board_position) -> 联动与二次虹吸 (board_link_siphon)
3. 第三层 个股交易执行：个股初筛 (stock_filter) -> 多维打分评级 (stock_score) -> 风控与仓位计算 (stock_trade_risk)

具备特性：
- 严格按照业务逻辑顺序串接数据流与参数依赖。
- 中途终止机制：任意环节输出 `flow_status=终止`，立即中断后续全部执行。
- 异常强健降级：顶层全局 try-except，运行时抛错安全转化为“全局异常”返回字典，绝不导致进程崩溃。
- 信息智能聚合：合并去重各子模块的缺失字段与全局风险，并格式化最终交易指令输出。
"""

import sys
import traceback
from typing import Dict, List, Any, Optional

# 导入第一层单例
from decision_framework.macro_score import macro_score
from decision_framework.macro_veto import macro_veto
from decision_framework.macro_revise import macro_revise

# 导入第二层单例
from decision_framework.board_rank import board_rank
from decision_framework.board_structure import board_structure
from decision_framework.board_style import board_style
from decision_framework.board_rotation import board_rotation
from decision_framework.board_position import board_position
from decision_framework.board_link_siphon import board_link_siphon

# 导入第三层单例
from decision_framework.stock_filter import stock_filter
from decision_framework.stock_score import stock_score
from decision_framework.stock_trade_risk import stock_trade_risk

# 导入公共依赖
from decision_framework.macro_query import macro_query
from config_loader import *
from decision_framework.decision_log import decision_log


class TotalWorkflow:
    """
    三层框架总调度顶层工作流单例类。
    """

    def run(
        self,
        pre_expect: Optional[Dict[str, Any]] = None,
        ext_data: Optional[Dict[str, Any]] = None,
        pos_positions: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        全流程总调度主入口。
        
        参数:
            pre_expect: 盘前预判情绪基准 (若为空，则启用默认中性配置)
            ext_data: 盘中修正的外部半导体或日内覆写数据
            pos_positions: 当前账户真实的板块持仓 (若为空，则读取本地 json 或默认为空仓)
            
        返回:
            标准返回字典格式 (含总流转状态、层级摘要、最终交易指令、风险汇总、去重数据缺失)
        """
        decision_log.info("🚀 [TotalWorkflow] 启动三层框架全流程总调度整合决策流...")
        
        # 1. 默认参数初始化
        pre_expect = pre_expect or {
            "up_down_ratio": 1.0,
            "top_board_change": 2.0,
            "limit_up_num": 15
        }
        
        # 汇总各环节数据缺失项、风险提示
        all_missing = []
        global_risks = []
        
        # 中间过程缓存容器
        macro_sum = {
            "operate_mode": "防守",
            "total_score": 0.0,
            "veto_trigger": "否",
            "revise_desc": "未执行"
        }
        
        board_sum = {
            "main_style": "未知",
            "rotate_strength": "未知",
            "siphon_level": "未知",
            "market_position": "0.0%"
        }
        
        # 顶层全局异常捕获
        try:
            # =========================================================================
            # 第一阶段：第一层宏观诊断
            # =========================================================================
            decision_log.info("🔹 [TotalWorkflow] 进入第一阶段：第一层宏观环境诊断分析")
            
            # (1) 宏观打分 (macro_score)
            score_res = macro_score.run()
            all_missing.extend(score_res.get("data_missing_list", []))
            
            macro_sum["total_score"] = score_res.get("total_score", 0.0)
            macro_sum["operate_mode"] = score_res.get("operate_mode", "防守")
            
            # 第一层打分决定终止拦截
            if score_res.get("flow_status") == "终止":
                reason = "第一层宏观打分决定流程终止(分数未达标或触发硬红灯)"
                decision_log.warning(f"🚨 [TotalWorkflow] 决策中断：{reason}")
                return self._build_terminate_result("中途终止", reason, macro_sum, board_sum, all_missing, global_risks)
                
            # (2) 大盘健康度评估 (一票否决机制已去除)
            veto_res = macro_veto.run()
            veto_triggered = veto_res.get("veto_result", {}).get("veto_triggered", False)
            veto_reason = veto_res.get("veto_result", {}).get("trigger_reason", "")
            all_missing.extend(veto_res.get("veto_result", {}).get("missing_list", []))
            all_missing.extend(veto_res.get("health_result", {}).get("data_missing", []))
            
            # 汇总健康度风险
            health_risks = veto_res.get("health_result", {}).get("risk_list", [])
            global_risks.extend(health_risks)
            
            # 兼容逻辑：若未来重新启用一票否决，此处保留阻断流转逻辑
            if veto_triggered:
                macro_sum["veto_trigger"] = "是"
                # 如果是一票否决中需要阻断的 (例如 "stop" 状态)
                if veto_res.get("veto_result", {}).get("flow_status") == "stop":
                    reason = f"第一层宏观一票否决触发强风控阻断: {veto_reason}"
                    macro_sum["operate_mode"] = "防守"
                    decision_log.warning(f"🚨 [TotalWorkflow] 决策中断：{reason}")
                    return self._build_terminate_result("中途终止", reason, macro_sum, board_sum, all_missing, global_risks)
            
            # (3) 盘中修正 (macro_revise)
            revise_res = macro_revise.run(score_res, pre_expect, ext_data)
            all_missing.extend(revise_res.get("data_missing_list", []))
            
            # 拼装盘中修正说明
            r_items = revise_res.get("revise_items", [])
            r_desc_list = []
            for item in r_items:
                r_desc_list.append(f"{item['rule_name']}: {item['status']} ({item['reason']})")
            macro_sum["revise_desc"] = " | ".join(r_desc_list) if r_desc_list else "未触发修正规则"
            
            # 更新修正后的操作模式
            revised_mode = revise_res.get("revised_mode", "谨慎")
            macro_sum["operate_mode"] = revised_mode
            
            # 盘中修正后决策终止拦截
            if revise_res.get("flow_status") == "终止":
                reason = f"第一层宏观盘中修正决定流程终止(操作模式纠偏为防守)"
                decision_log.warning(f"🚨 [TotalWorkflow] 决策中断：{reason}")
                return self._build_terminate_result("中途终止", reason, macro_sum, board_sum, all_missing, global_risks)
                
            decision_log.info("✅ [TotalWorkflow] 第一阶段宏观诊断顺利完成。")
            
            # =========================================================================
            # 第二阶段：第二层板块风格轮动
            # =========================================================================
            decision_log.info("🔹 [TotalWorkflow] 进入第二阶段：第二层板块风格与轮动分析")
            
            # (1) 板块初筛与打分 (board_rank)
            rank_res = board_rank.run(pre_expect)
            all_missing.extend(rank_res.get("data_missing_list", []))
            
            if rank_res.get("flow_status") == "终止":
                reason = "第二层板块评级打分触发流程终止(无可操作候选板块)"
                decision_log.warning(f"🚨 [TotalWorkflow] 决策中断：{reason}")
                return self._build_terminate_result("中途终止", reason, macro_sum, board_sum, all_missing, global_risks)
                
            board_list = rank_res.get("board_list", [])
            
            # (2) 遍历候选板块，逐个判定中军结构 (board_structure)
            structure_summaries = []
            for board in board_list:
                b_name = board["board_name"]
                struct_res = board_structure.run(b_name)
                all_missing.extend(struct_res.get("data_missing_list", []))
                
                # 若结构出现终止阻断（正常板块结构本身不会硬拦截，但需遵循终止规范）
                if struct_res.get("flow_status") == "终止":
                    reason = f"第二层板块 [{b_name}] 结构评定触发流程终止: {struct_res.get('ladder_reason')}"
                    decision_log.warning(f"🚨 [TotalWorkflow] 决策中断：{reason}")
                    return self._build_terminate_result("中途终止", reason, macro_sum, board_sum, all_missing, global_risks)
                
                structure_summaries.append({
                    "board_name": b_name,
                    "composite_rating": struct_res.get("composite_rating", "未知")
                })
                
            # (3) 板块风格热度 (board_style)
            style_res = board_style.run(rank_res)
            all_missing.extend(style_res.get("data_missing_list", []))
            
            if style_res.get("flow_status") == "终止":
                reason = "第二层风格热度评估决定流程终止"
                decision_log.warning(f"🚨 [TotalWorkflow] 决策中断：{reason}")
                return self._build_terminate_result("中途终止", reason, macro_sum, board_sum, all_missing, global_risks)
            
            # 提取最强风格大类
            style_group = style_res.get("style_group", [])
            strong_styles = [s["style_name"] for s in style_group if s.get("intraday_strength") == "强势" or s.get("cross_day_strength") == "强势"]
            if strong_styles:
                board_sum["main_style"] = ", ".join(list(set(strong_styles)))
            else:
                max_score_style = None
                max_score = -1.0
                for s in style_group:
                    score = max(s.get("intra_score", 0.0), s.get("cross_score", 0.0))
                    if score > max_score:
                        max_score = score
                        max_score_style = s["style_name"]
                board_sum["main_style"] = f"无强势主线 (相对最强: {max_score_style})" if max_score_style else "无"
                
            # (4) 轮动信号评估 (board_rotation)
            rot_res = board_rotation.run(style_res)
            all_missing.extend(rot_res.get("data_missing_list", []))
            
            if rot_res.get("flow_status") == "终止":
                reason = "第二层板块轮动分析决定流程终止"
                decision_log.warning(f"🚨 [TotalWorkflow] 决策中断：{reason}")
                return self._build_terminate_result("中途终止", reason, macro_sum, board_sum, all_missing, global_risks)
                
            board_sum["rotate_strength"] = rot_res.get("rotate_strength", "未知")
            
            # (5) 仓位合规性决策 (board_position)
            pos_res = board_position.run(style_res, rot_res, pos_positions)
            all_missing.extend(pos_res.get("data_missing_list", []))
            
            if pos_res.get("flow_status") == "终止":
                reason = "第二层板块仓位管控决策决定流程终止(触发硬性爆仓或违规线拦截)"
                decision_log.warning(f"🚨 [TotalWorkflow] 决策中断：{reason}")
                return self._build_terminate_result("中途终止", reason, macro_sum, board_sum, all_missing, global_risks)
                
            total_market_pos = pos_res.get("market_total_pos", 0.0)
            board_sum["market_position"] = f"{total_market_pos:.2%}"
            
            # (6) 联动与二次资金虹吸 (board_link_siphon)
            siphon_res = board_link_siphon.run(style_res, rot_res, pos_res)
            all_missing.extend(siphon_res.get("data_missing_list", []))
            
            # 汇总风控警报
            if siphon_res.get("risk_warn"):
                global_risks.append(siphon_res["risk_warn"])
                
            board_sum["siphon_level"] = siphon_res.get("siphon_info", {}).get("siphon_level", "无虹吸")
            
            # 二次虹吸决定终止拦截 (如强虹吸风控硬拦截)
            if siphon_res.get("flow_status") == "终止":
                reason = f"第二层联动收官拦截: 检测到强资金虹吸效应进行硬拦截. 警报: {siphon_res.get('risk_warn')}"
                decision_log.warning(f"🚨 [TotalWorkflow] 决策中断：{reason}")
                return self._build_terminate_result("中途终止", reason, macro_sum, board_sum, all_missing, global_risks)
                
            decision_log.info("✅ [TotalWorkflow] 第二阶段板块分析顺利完成。")
            
            # =========================================================================
            # 第三阶段：第三层个股交易执行
            # =========================================================================
            decision_log.info("🔹 [TotalWorkflow] 进入第三阶段：第三层个股初筛打分与风控计算")
            
            # (1) 个股初筛 (stock_filter)
            filter_res = stock_filter.run(siphon_res)
            all_missing.extend(filter_res.get("data_missing_list", []))
            
            if filter_res.get("flow_status") == "终止":
                reason = "第三层个股初筛阶段决定流程终止"
                decision_log.warning(f"🚨 [TotalWorkflow] 决策中断：{reason}")
                return self._build_terminate_result("中途终止", reason, macro_sum, board_sum, all_missing, global_risks)
                
            # (2) 个股多维量化打分 (stock_score)
            stock_score_res = stock_score.run(filter_res)
            all_missing.extend(stock_score_res.get("data_missing_list", []))
            
            if stock_score_res.get("flow_status") == "终止":
                reason = "第三层个股量化评分阶段决定流程终止"
                decision_log.warning(f"🚨 [TotalWorkflow] 决策中断：{reason}")
                return self._build_terminate_result("中途终止", reason, macro_sum, board_sum, all_missing, global_risks)
                
            # (3) 技术点位止损止盈与动态仓位 (stock_trade_risk)
            trade_res = stock_trade_risk.run(stock_score_res, siphon_res, revised_mode)
            all_missing.extend(trade_res.get("data_missing_list", []))
            
            # 汇总个股全局风控提示
            if trade_res.get("global_risk_tip"):
                global_risks.append(trade_res["global_risk_tip"])
                
            if trade_res.get("flow_status") == "终止":
                reason = "第三层风控与仓位阶段决定流程终止"
                decision_log.warning(f"🚨 [TotalWorkflow] 决策中断：{reason}")
                return self._build_terminate_result("中途终止", reason, macro_sum, board_sum, all_missing, global_risks)
                
            decision_log.info("✅ [TotalWorkflow] 第三阶段个股交易执行计算完毕。")
            
            # =========================================================================
            # 结果汇总与最终组装
            # =========================================================================
            # A. 提取标准化最终交易指令，关联个股评级
            score_level_map = {}
            for item in stock_score_res.get("stock_score_list", []):
                score_level_map[item["stock_code"]] = item.get("stock_level", "一般标的")
                
            final_trade_list = []
            for item in trade_res.get("trade_list", []):
                code = item["stock_code"]
                final_trade_list.append({
                    "stock_code": code,
                    "stock_name": item["stock_name"],
                    "stock_level": score_level_map.get(code, "一般标的"),
                    "entry_zone": item["entry_zone"],
                    "support_price": item["support_price"],
                    "pressure_price": item["pressure_price"],
                    "stop_loss_price": item["stop_loss_price"],
                    "first_profit_price": item["first_profit_price"],
                    "second_profit_price": item["second_profit_price"],
                    "suggest_position": item["suggest_position"],
                    "risk_tip": item["position_detail"]
                })
                
            # B. 全链路数据缺失和风险列表去重
            clean_missing = sorted(list(set(all_missing)))
            clean_risks = sorted(list(set(global_risks)))
            
            decision_log.info("🎉 [TotalWorkflow] 三层决策框架全链路执行完毕。决策流正常完成。")
            
            return {
                "total_flow_status": "正常完成",
                "terminate_reason": "",
                "macro_summary": macro_sum,
                "board_summary": board_sum,
                "final_trade_list": final_trade_list,
                "global_risk_list": clean_risks,
                "all_data_missing": clean_missing
            }
            
        except Exception as e:
            # 记录全局异常日志
            ex_type, ex_val, ex_tb = sys.exc_info()
            tb_str = "".join(traceback.format_exception(ex_type, ex_val, ex_tb))
            decision_log.error(f"❌ [TotalWorkflow] 全局调度捕获未处理异常:\n{tb_str}")
            
            clean_missing = sorted(list(set(all_missing)))
            clean_risks = sorted(list(set(global_risks)))
            clean_risks.append(f"【系统严重故障】顶层捕获到未处理异常: {str(e)}")
            
            return {
                "total_flow_status": "全局异常",
                "terminate_reason": f"程序执行严重故障: {str(e)}\n{tb_str}",
                "macro_summary": macro_sum,
                "board_summary": board_sum,
                "final_trade_list": [],
                "global_risk_list": clean_risks,
                "all_data_missing": clean_missing
            }

    def _build_terminate_result(
        self,
        status: str,
        reason: str,
        macro_sum: Dict[str, Any],
        board_sum: Dict[str, Any],
        all_missing: List[str],
        global_risks: List[str],
    ) -> Dict[str, Any]:
        """
        中途阻断时的快速出局数据构造辅助方法。
        """
        # 对风险和数据缺失去重
        clean_missing = sorted(list(set(all_missing)))
        clean_risks = sorted(list(set(global_risks)))
        clean_risks.append(f"【流程风控阻断】: {reason}")
        
        return {
            "total_flow_status": status,
            "terminate_reason": reason,
            "macro_summary": macro_sum,
            "board_summary": board_sum,
            "final_trade_list": [],
            "global_risk_list": clean_risks,
            "all_data_missing": clean_missing
        }


# 对外导出全局唯一调度单例对象
total_workflow = TotalWorkflow()

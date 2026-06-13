# -*- coding: utf-8 -*-
"""
board_rank.py —— 第一层宏观环境诊断：板块初筛、优先级打分与资金虹吸风险控制引擎
========================================================================

本模块为第一层的收尾模块，根据行业板块的主力资金流向与五大特质进行初筛，
并通过 4 大维度（主力资金持续强度、涨停覆盖率与梯队完整度、中军消耗效应、周期位置）
对板块进行打分与优先级划分，最后引入资金虹吸规则进行二次仓位风控调整。
"""

from typing import Dict, List, Any, Optional, Tuple
from decision_framework.macro_query import macro_query
from decision_framework.macro_score import macro_score
from decision_framework.macro_veto import macro_veto
from decision_framework.macro_revise import macro_revise
from config_loader import *
from decision_framework.decision_log import decision_log


class BoardRank:
    """
    板块初筛、打分优先级排序与资金虹吸规则单例类。
    """

    def _get_val(self, item: Dict[str, Any], keys: List[str], default=None) -> Any:
        """
        辅助函数：兼容中英文多种字段键名
        """
        for k in keys:
            if k in item:
                return item[k]
        return default

    def filter_board(
        self, mock_data: Optional[List[Dict[str, Any]]] = None, missing_list: List[str] = None
    ) -> List[Dict[str, Any]]:
        """
        1. 板块初筛
        
        筛选当日资金流入前 TOP_BOARD_COUNT (5) 个板块；
        并根据有效性校验（涨停潮、龙头高度、梯队）与反向透支校验筛选，最终保留 1~3 个有效板块。
        """
        try:
            # 1. 获取全板块数据
            boards = []
            if mock_data is not None:
                boards = mock_data
            else:
                boards = macro_query.get_board_data()

            if not boards:
                if missing_list is not None:
                    missing_list.append("全板块资金与指标数据(board_money_flow)")
                decision_log.warning("⚠️ [BoardRank] 未能获取到板块资金流向数据，板块排序流程降级跳过")
                return []

            # 2. 资金排名过滤：取当日主力资金净流入排名前 TOP_BOARD_COUNT (5) 的板块
            # 当日流入净额字段支持: net_amount, flow_amount, 今日主力净流入额, 主力净流入
            net_amt_keys = ["net_amount", "flow_amount", "今日主力净流入额", "主力净流入"]
            
            # 过滤并格式化金额为 float
            valid_boards = []
            for b in boards:
                val = self._get_val(b, net_amt_keys)
                if val is not None:
                    try:
                        b["_net_amount"] = float(val)
                        valid_boards.append(b)
                    except ValueError:
                        pass

            if not valid_boards:
                decision_log.warning("⚠️ [BoardRank] 板块当日净流入字段格式异常，无法完成初筛。")
                return []

            # 从大到小排序，取前 5
            valid_boards.sort(key=lambda x: x["_net_amount"], reverse=True)
            top_boards = valid_boards[:TOP_BOARD_COUNT]
            
            decision_log.info(f"ℹ️ [BoardRank] 资金排序前 {len(top_boards)} 个板块: "
                              f"{[self._get_val(tb, ['board_name', '名称', '板块名称']) for tb in top_boards]}")

            # 3. 对这前5个板块进行有效性与透支校验
            filtered_boards = []
            
            # 有效性字段支持
            limit_up_keys = ["limit_up_count", "limit_up", "涨停数", "涨停家数"]
            height_keys = ["leader_height", "height", "龙头高度", "最高连板"]
            complete_keys = ["tier_complete", "is_complete", "梯队完整", "梯队完整度"]
            year_rise_keys = ["year_rise", "year_pct", "年内涨幅", "今年涨跌幅"]
            hist_keys = ["historical_match", "hist_match", "历史匹配", "爆发规律"]

            for b in top_boards:
                name = self._get_val(b, ["board_name", "名称", "板块名称"]) or "未知板块"
                
                limit_up = self._get_val(b, limit_up_keys, 0)
                height = self._get_val(b, height_keys, 0)
                is_complete = self._get_val(b, complete_keys, True)
                year_rise = self._get_val(b, year_rise_keys, 0.0)
                hist_match = self._get_val(b, hist_keys, True)

                # 3.1 反向校验：年内涨幅是否透支 (超50%)
                if year_rise > 0.50:
                    decision_log.warning(f"⚠️ [BoardRank] 板块 [{name}] 年内累计涨幅 {year_rise:.1%} 已严重透支，初筛剔除")
                    continue

                # 3.2 有效性校验：是否符合资金与投机特征（默认无相关字段时代表通过）
                is_valid = True
                if limit_up is not None and limit_up < 2:
                    is_valid = False
                if height is not None and height < 3:
                    is_valid = False
                if is_complete is not None and not is_complete:
                    is_valid = False

                if not is_valid:
                    decision_log.info(f"ℹ️ [BoardRank] 板块 [{name}] 有效性校验未通过(涨停数:{limit_up}, 高度:{height}, 完整:{is_complete})")
                    continue

                # 3.3 历史回溯匹配
                if hist_match is not None and not hist_match:
                    decision_log.info(f"ℹ️ [BoardRank] 板块 [{name}] 爆发历史规律不匹配，初筛剔除")
                    continue

                filtered_boards.append(b)

            # 4. 最终保留 1~3 个有效观察板块
            # 如果符合要求的板块为空，为了不让策略在资金龙头下完全空置，
            # 降级保留资金第一且未透支的那个板块
            if not filtered_boards and top_boards:
                for b in top_boards:
                    year_rise = self._get_val(b, year_rise_keys, 0.0)
                    if year_rise <= 0.50:
                        name = self._get_val(b, ["board_name", "名称", "板块名称"]) or "未知板块"
                        decision_log.warning(f"⚠️ [BoardRank] 有效性过滤后无板块通过，降级保留资金第一且未透支板块: [{name}]")
                        filtered_boards.append(b)
                        break

            # 最终截取前 3 个
            final_observation = filtered_boards[:3]
            decision_log.info(f"✅ [BoardRank] 板块初筛完成，选出观察板块: "
                              f"{[self._get_val(ob, ['board_name', '名称', '板块名称']) for ob in final_observation]}")
            return final_observation

        except Exception as e:
            decision_log.error(f"❌ [BoardRank] 板块初筛发生异常: {e}")
            return []

    def calc_single_dim(self, board_item: Dict[str, Any], missing_list: List[str]) -> Tuple[Dict[str, float], float]:
        """
        2. 四大维度单板块打分 (固定权重)
        
        总分 = (维度1得分 * 2) + 维度2得分 + 维度3得分 + 维度4得分
        数据缺失单维度打 0.5 分并记录缺失。
        """
        board_name = self._get_val(board_item, ["board_name", "名称", "板块名称"]) or "未知板块"
        dim_scores = {}
        
        # 维度 1：主力资金持续强度 (权重 2)
        flow_5d = self._get_val(board_item, ["flow_5d", "flow_5d_amount", "近5日净流入", "5日主力净流入"])
        if flow_5d is None:
            dim_scores["dim1"] = 0.5
            missing_list.append(f"板块 [{board_name}] 5日资金净流入")
        else:
            try:
                flow_val = float(flow_5d)
                if flow_val > FLOW_5D_100B:
                    dim_scores["dim1"] = BOARD_SCORE_FULL
                elif flow_val >= FLOW_5D_50B:
                    dim_scores["dim1"] = BOARD_SCORE_HALF
                else:
                    dim_scores["dim1"] = BOARD_SCORE_ZERO
            except ValueError:
                dim_scores["dim1"] = 0.5
                missing_list.append(f"板块 [{board_name}] 5日资金净流入(格式错误)")

        # 维度 2：涨停覆盖率与梯队完整度 (权重 1)
        cover_ratio = self._get_val(board_item, ["cover_ratio", "coverage", "涨停覆盖率"])
        tier_status = self._get_val(board_item, ["tier_status", "tier_level", "梯队完整度", "梯队状态"])
        
        if cover_ratio is None or tier_status is None:
            dim_scores["dim2"] = 0.5
            missing_list.append(f"板块 [{board_name}] 涨停覆盖率或梯队状态")
        else:
            try:
                cov_val = float(cover_ratio)
                # 梯队状态支持：完整 (2)、基本完整 (1)、断层 (0) 或字符串
                is_full_tier = tier_status in ["完整", 2, "2"]
                is_half_tier = tier_status in ["基本完整", 1, "1"]
                
                if cov_val > COVER_FULL and is_full_tier:
                    dim_scores["dim2"] = BOARD_SCORE_FULL
                elif cov_val >= COVER_HALF and (is_full_tier or is_half_tier):
                    dim_scores["dim2"] = BOARD_SCORE_HALF
                else:
                    dim_scores["dim2"] = BOARD_SCORE_ZERO
            except ValueError:
                dim_scores["dim2"] = 0.5
                missing_list.append(f"板块 [{board_name}] 涨停覆盖率数据异常")

        # 维度 3：中军消耗效应 (权重 1)
        # 支持："未消耗" (2)、"消耗但次日走强" (1)、"消耗且断板" (0)
        sentry_status = self._get_val(board_item, ["sentry_status", "sentry", "中军消耗", "中军状态"])
        if sentry_status is None:
            dim_scores["dim3"] = 0.5
            missing_list.append(f"板块 [{board_name}] 中军消耗状态")
        else:
            status_str = str(sentry_status)
            if status_str in ["未消耗", "2", "True"]:
                dim_scores["dim3"] = BOARD_SCORE_FULL
            elif status_str in ["消耗但次日走强", "1"]:
                dim_scores["dim3"] = BOARD_SCORE_HALF
            elif status_str in ["消耗且断板", "0", "False"]:
                dim_scores["dim3"] = BOARD_SCORE_ZERO
            else:
                dim_scores["dim3"] = 0.5

        # 维度 4：板块周期性位置 (权重 1)
        retreat_ratio = self._get_val(board_item, ["retreat_ratio", "retreat", "回撤幅度", "高点回撤"])
        week_rise = self._get_val(board_item, ["week_rise", "week_pct", "上周涨幅", "前周涨跌幅"])
        
        if retreat_ratio is None or week_rise is None:
            dim_scores["dim4"] = 0.5
            missing_list.append(f"板块 [{board_name}] 回撤幅度或上周涨幅")
        else:
            try:
                ret_val = float(retreat_ratio)
                rise_val = float(week_rise)
                
                if ret_val > RETREAT_3M and rise_val < WEEK_RISE_LIMIT:
                    dim_scores["dim4"] = BOARD_SCORE_FULL
                else:
                    dim_scores["dim4"] = BOARD_SCORE_ZERO
            except ValueError:
                dim_scores["dim4"] = 0.5
                missing_list.append(f"板块 [{board_name}] 回撤与涨幅数据异常")

        # 计算加权总分
        total_score = (dim_scores["dim1"] * 2.0) + dim_scores["dim2"] + dim_scores["dim3"] + dim_scores["dim4"]
        
        decision_log.info(
            f"📊 [BoardRank] 板块 [{board_name}] 打分明细 -> "
            f"资金强度(2x): {dim_scores['dim1']:.1f} | 覆盖率(1x): {dim_scores['dim2']:.1f} | "
            f"中军消耗(1x): {dim_scores['dim3']:.1f} | 周期位置(1x): {dim_scores['dim4']:.1f} | "
            f"总分: {total_score:.1f}"
        )
        return dim_scores, total_score

    def calc_board_total(self, board_name: str, total_score: float) -> Tuple[str, str]:
        """
        3. 板块优先级划分
        
        - 总分 ≥ 4.0  → 第一优先级：全策略可用，板块仓位不压缩
        - 2.5 ≤ 总分 ≤ 3.5 → 第二优先级：策略B仓位压缩50%，策略A正常执行
        - 总分 < 2.5 → 第三优先级：仅允许策略A，策略B暂停
        """
        if total_score >= 4.0:
            priority = "第一优先级"
            strategy_rule = "全策略可用，板块仓位不压缩"
        elif total_score >= 2.5:
            priority = "第二优先级"
            strategy_rule = "策略B仓位压缩50%，策略A正常执行"
        else:
            priority = "第三优先级"
            strategy_rule = "仅允许策略A，策略B暂停"
            
        decision_log.info(f"ℹ️ [BoardRank] 板块 [{board_name}] 最终划分: {priority} ({strategy_rule})")
        return priority, strategy_rule

    def check_siphon(self, board_list: List[Dict[str, Any]]) -> Tuple[str, str]:
        """
        4. 资金虹吸风险判定
        
        若第一名板块资金 / 第二名板块资金 > SIPHON_MULTIPLE (2.0)，
        判定存在虹吸风险。低优先级板块自动暂停策略B。
        """
        if len(board_list) < 2:
            return "无", "候选板块不足两个，跳过资金虹吸校验"

        try:
            # 这里的板已按资金流入进行排序
            b1 = board_list[0]
            b2 = board_list[1]
            
            name1 = b1["board_name"]
            name2 = b2["board_name"]
            
            fund1 = b1["_net_amount"]
            fund2 = b2["_net_amount"]
            
            if fund2 <= 0:
                # 第二名未有正资金流入，难以衡量吸金倍数或吸金极强
                if fund1 > 0:
                    siphon_ratio = float("inf")
                else:
                    return "无", "大盘资金均呈流出态势，无虹吸风险"
            else:
                siphon_ratio = fund1 / fund2

            if siphon_ratio > SIPHON_MULTIPLE:
                siphon_risk = "有"
                siphon_desc = (
                    f"存在资金虹吸风险：第一名板块 [{name1}] 当日资金({fund1/1e8:.2f}亿) "
                    f"超过第二名 [{name2}] 当日资金({fund2/1e8:.2f}亿) 的 {siphon_ratio:.1f} 倍 "
                    f"(阈值 {SIPHON_MULTIPLE} 倍)。非第一优先级板块自动暂停策略B，仅可寻找低位补涨。"
                )
                
                # 强制动作：将所有“第二优先级”和“第三优先级”的板块策略规则更新
                for b in board_list:
                    if b["priority"] in ["第二优先级", "第三优先级"]:
                        b["strategy_rule"] = "受资金虹吸风险限制，策略B暂停，仅可寻找低位补涨"
                        decision_log.warning(f"🔄 [BoardRank] 由于触发虹吸风控，板块 [{b['board_name']}] 被锁定为: {b['strategy_rule']}")
            else:
                siphon_risk = "无"
                siphon_desc = f"资金分配平稳：板块资金比值 {siphon_ratio:.2f} 倍，未超阈值 {SIPHON_MULTIPLE} 倍"

            decision_log.info(f"ℹ️ [BoardRank] 资金虹吸校验结果: {siphon_risk} | {siphon_desc}")
            return siphon_risk, siphon_desc

        except Exception as e:
            decision_log.error(f"❌ [BoardRank] 校验资金虹吸异常: {e}")
            return "无", f"校验异常: {str(e)}"

    def run(self, pre_expect: Optional[Dict[str, Any]] = None, mock_data: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        统一对外工作流入口
        
        参数:
          - pre_expect: 盘前情绪指标预判字典
          - mock_data: 显式传入的板块数据 (测试与特殊实盘用)
        """
        decision_log.info("🚀 [BoardRank] 启动板块筛选、打分排序与虹吸风控流程...")
        data_missing_list = []

        # 1. 宏观防守拦截规则
        try:
            # 运行打分卡
            pre_score = macro_score.run()
            # 运行修正
            pre_expect_val = pre_expect or {
                "up_down_ratio": 1.0,
                "top_board_change": 2.0,
                "limit_up_num": 15
            }
            revise_res = macro_revise.run(pre_score, pre_expect_val)
            final_mode = revise_res.get("revised_mode", "谨慎")

            if final_mode == "防守":
                decision_log.warning("🚨 [BoardRank] 宏观最终决策为 [防守] 模式，依据存疑即止与防御纪律，板块排序流程直接终止。")
                return {
                    "flow_status": "终止",
                    "board_list": [],
                    "siphon_risk": "无",
                    "siphon_desc": "宏观防守模式，未启动虹吸校验",
                    "data_missing_list": []
                }
        except Exception as e:
            decision_log.error(f"❌ [BoardRank] 前置宏观决策评估出错: {e}，安全阻断板块流程")
            return {
                "flow_status": "终止",
                "board_list": [],
                "siphon_risk": "无",
                "siphon_desc": f"前置决策异常: {str(e)}",
                "data_missing_list": ["宏观前置评估异常"]
            }

        # 2. 板块初筛
        obs_boards = self.filter_board(mock_data, data_missing_list)
        if not obs_boards:
            return {
                "flow_status": "继续",
                "board_list": [],
                "siphon_risk": "无",
                "siphon_desc": "无初筛符合条件板块",
                "data_missing_list": data_missing_list
            }

        # 3. 逐个板块打分与优先级划分
        final_board_list = []
        for b in obs_boards:
            name = self._get_val(b, ["board_name", "名称", "板块名称"]) or "未知板块"
            
            # 打分
            dim_detail, total_score = self.calc_single_dim(b, data_missing_list)
            # 优先级
            priority, strategy_rule = self.calc_board_total(name, total_score)
            
            final_board_list.append({
                "board_name": name,
                "dim_detail": dim_detail,
                "total_score": total_score,
                "priority": priority,
                "strategy_rule": strategy_rule,
                "_net_amount": b["_net_amount"]  # 辅助用于虹吸排序
            })

        # 4. 资金虹吸风险判定与策略调整
        siphon_risk, siphon_desc = self.check_siphon(final_board_list)

        # 5. 格式化并清理辅助内部键，对外组装返回
        for fb in final_board_list:
            fb.pop("_net_amount", None)

        decision_log.info(f"✅ [BoardRank] 板块工作流执行完毕。候选数: {len(final_board_list)}，是否存在虹吸风险: {siphon_risk}")
        
        return {
            "flow_status": "继续",
            "board_list": final_board_list,
            "siphon_risk": siphon_risk,
            "siphon_desc": siphon_desc,
            "data_missing_list": list(set(data_missing_list))
        }


# 对外提供全局单例对象
board_rank = BoardRank()

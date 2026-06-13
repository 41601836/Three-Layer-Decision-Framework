# -*- coding: utf-8 -*-
"""
macro_revise.py —— 第一层宏观环境诊断：盘中实时修正与决策调整引擎
===========================================================

基于盘前综合决策结果与 10:30/14:00 盘中快照数据，独立实现三大盘中实时修正规则：
1. 半日数据线性外推禁止规则 (避免缩量行情直接错杀降级)
2. 隔夜外盘承接力量化规则 (检测超预期承接，上调板块优先级并推翻谨慎/暂停判定)
3. 半日情绪实时校验规则 (检测实际情绪对盘前预判的偏离度，即时纠偏操作模式)

本模块与打分、否决模块完全解耦，不篡改盘前原始打分数据，仅做最终操作模式的增量叠加与修正。
"""

import math
from typing import Dict, List, Any, Optional, Tuple
from db.dao import dao
from decision_framework.macro_query import macro_query
from config_loader import *
from decision_framework.decision_log import decision_log


class MacroRevise:
    """
    第一层盘中实时修正引擎。
    提供统一入口 run() 执行所有盘中修正项。
    """

    def _get_yesterday_amount(self, trade_date: str) -> Optional[float]:
        """
        辅助方法：从数据库中查询指定交易日的前一交易日全天成交额。
        """
        try:
            # 清洗日期格式，统一为 YYYYMMDD
            clean_date = trade_date.replace("-", "")
            
            conn = dao.get_conn()
            cursor = conn.cursor()
            
            # 1. 查找前一个最近的交易日
            sql_prev_date = "SELECT MAX(trade_date) FROM daily_prices WHERE trade_date < ?"
            cursor.execute(sql_prev_date, (clean_date,))
            row_date = cursor.fetchone()
            if not row_date or not row_date[0]:
                decision_log.warning(f"⚠️ [MacroRevise] 数据库中未找到小于 {clean_date} 的前一日交易日期")
                return None
            prev_date = row_date[0]
            
            # 2. 查询该交易日的个股成交额总和（即两市总成交额）
            sql_amt = "SELECT SUM(amount) FROM daily_prices WHERE trade_date = ?"
            cursor.execute(sql_amt, (prev_date,))
            row_amt = cursor.fetchone()
            if row_amt and row_amt[0] is not None:
                amt = float(row_amt[0])
                decision_log.info(f"ℹ️ [MacroRevise] 获取前一交易日({prev_date})成交额: {amt / 1e8:.2f} 亿")
                return amt
            return None
        except Exception as e:
            decision_log.error(f"❌ [MacroRevise] 查询前一交易日成交额异常: {e}")
            return None
        finally:
            if 'conn' in locals():
                conn.close()

    def check_volume_extrapolate(
        self, snapshot: Dict[str, Any], missing_list: List[str]
    ) -> Tuple[bool, Optional[str], Optional[float]]:
        """
        规则1：半日数据线性外推禁止规则
        
        时间节点：开盘后 10:00 (依托 10:30 快照数据) 评估成交额缩量比例。
        缩量计算：线性外推全天成交额相比昨日全天的降幅。
        判定：
          - 10:30 半日成交额较昨日缩量 > VOLUME_SHRINK_RATIO (15%)，禁止直接降级为谨慎模式，需等待午间数据；
          - 14:00 (午间全量数据) 如果缩量幅度收窄 (<= 15%)，支持将早盘的悲观降级反转回原有评级。
          
        返回: (is_triggered, status_desc, shrink_ratio)
        """
        try:
            # 1. 数据校验与提取
            if snapshot.get("data_missing", True):
                missing_list.append("成交额外推规则: 10:30/14:00 市场快照")
                return False, "快照数据缺失，跳过成交额外推校验", None

            trade_date = snapshot.get("trade_date")
            snapshot_time = snapshot.get("snapshot_time") or ""
            half_day_amount = snapshot.get("total_amount")

            if not trade_date or half_day_amount is None:
                missing_list.append("成交额外推规则: 快照日期或成交额字段")
                return False, "成交额或交易日数据缺失，跳过校验", None

            yesterday_amount = self._get_yesterday_amount(trade_date)
            if not yesterday_amount or yesterday_amount <= 0:
                missing_list.append(f"成交额外推规则: {trade_date} 的前一日全天成交额")
                return False, "前一交易日全天成交额缺失，跳过校验", None

            # 2. 区分 10:30 与 14:00 计算外推值
            is_1400 = "14:00" in snapshot_time
            if is_1400:
                # 14:00 已经交易 3 小时 (9:30-11:30, 13:00-14:00)，外推全天 = 14:00累计成交 * 4/3
                extrapolated_amount = half_day_amount * (4.0 / 3.0)
                time_label = "14:00 盘中"
            else:
                # 默认按 10:30 半日成交 (交易 2 小时) 外推全天 = 半日成交 * 2
                extrapolated_amount = half_day_amount * 2.0
                time_label = "10:30 早盘"

            # 计算缩量比例 (昨日相比外推全天)
            shrink_ratio = (yesterday_amount - extrapolated_amount) / yesterday_amount

            # 3. 判定逻辑
            if not is_1400:
                # 10:30 早盘规则
                if shrink_ratio > VOLUME_SHRINK_RATIO:
                    reason = (
                        f"早盘缩量超标: 昨日成交额为 {yesterday_amount/1e8:.2f}亿，"
                        f"10:30成交额为 {half_day_amount/1e8:.2f}亿，"
                        f"线性外推全天成交额较昨日缩量 {shrink_ratio:.1%} (阈值 {VOLUME_SHRINK_RATIO:.1%})。"
                        f"触发半日外推禁止规则：禁止直接降级为谨慎模式。"
                    )
                    decision_log.warning(f"⚠️ [MacroRevise] {reason}")
                    return True, "severe_shrink", shrink_ratio
                else:
                    reason = f"早盘成交额稳健，缩量比例 {shrink_ratio:.1%} 未超阈值 {VOLUME_SHRINK_RATIO:.1%}。"
                    decision_log.info(f"ℹ️ [MacroRevise] {reason}")
                    return False, "normal", shrink_ratio
            else:
                # 14:00 午盘反转判定
                if shrink_ratio <= VOLUME_SHRINK_RATIO:
                    reason = (
                        f"午间缩量收窄: 14:00成交额为 {half_day_amount/1e8:.2f}亿，"
                        f"外推全天成交额较昨日缩量为 {shrink_ratio:.1%}，已收窄至阈值 {VOLUME_SHRINK_RATIO:.1%} 以内。"
                        f"满足反转条件，支持将早盘的悲观降级反转为原有评级。"
                    )
                    decision_log.warning(f"⚠️ [MacroRevise] {reason}")
                    return True, "volume_recovery", shrink_ratio
                else:
                    reason = f"午后成交量依然低迷，外推全天缩量比例 {shrink_ratio:.1%} 超出阈值。"
                    decision_log.info(f"ℹ️ [MacroRevise] {reason}")
                    return False, "still_shrink", shrink_ratio

        except Exception as e:
            decision_log.error(f"❌ [MacroRevise] 校验成交额外推规则发生异常: {e}")
            return False, f"执行异常: {str(e)}", None

    def check_foreign_accept(
        self, snapshot: Dict[str, Any], global_macro: Dict[str, Any], missing_list: List[str], ext_data: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Optional[str], Optional[float]]:
        """
        规则2：隔夜外盘承接力量化规则
        
        前置条件：隔夜海外芯片/半导体板块单日跌幅 > FOREIGN_BOARD_DROP (3.0%)。
        计算比值：A股同板块日内跌幅 ÷ 外盘跌幅。
        判定：
          - 比值 < ACCEPT_RATIO_THRESHOLD (30% 或 0.3) -> 承接力超预期，上调板块优先级，且原有“谨慎/暂停(防守)”判定可推翻。
        """
        try:
            # 1. 提取海外芯片跌幅与 A 股同板块日内跌幅
            foreign_drop = None
            a_drop = None

            # 优先从 ext_data 提取，实现最大容错与测试便利
            if ext_data:
                foreign_drop = ext_data.get("foreign_semiconductor_drop")
                a_drop = ext_data.get("a_semiconductor_drop")

            # 若 ext_data 无，则从数据库扩展字段尝试提取 (约定 ext1 字段)
            if foreign_drop is None and not global_macro.get("data_missing", True):
                val = global_macro.get("ext1")
                if val:
                    try:
                        foreign_drop = abs(float(val))
                    except ValueError:
                        pass

            if a_drop is None and not snapshot.get("data_missing", True):
                val = snapshot.get("ext1")
                if val:
                    try:
                        a_drop = abs(float(val))
                    except ValueError:
                        pass

            # 数据缺失判定
            if foreign_drop is None or a_drop is None:
                missing_list.append("外盘承接力规则: 海外半导体跌幅或A股半导体跌幅")
                return False, "半导体板块对比数据缺失，跳过承接力校验", None

            # 2. 判断前置条件
            # 外盘跌幅取正值以方便计算比较
            foreign_drop_abs = abs(foreign_drop)
            a_drop_abs = abs(a_drop)

            if foreign_drop_abs <= FOREIGN_BOARD_DROP:
                reason = f"外盘芯片单日跌幅 {foreign_drop_abs:.2f}% 未达到前置阈值 {FOREIGN_BOARD_DROP:.1f}%，无需启动承接力校验。"
                decision_log.info(f"ℹ️ [MacroRevise] {reason}")
                return False, "pre_condition_not_met", None

            if foreign_drop_abs == 0:
                return False, "外盘跌幅为零，无法计算比值", None

            # 3. 计算比值
            accept_ratio = a_drop_abs / foreign_drop_abs

            # 4. 判定
            if accept_ratio < ACCEPT_RATIO_THRESHOLD:
                reason = (
                    f"A股承接力超预期: 隔夜外盘跌 {foreign_drop_abs:.2f}%，"
                    f"A股仅跌 {a_drop_abs:.2f}%，承接力比值 {accept_ratio:.1%} 小于阈值 {ACCEPT_RATIO_THRESHOLD:.1%}。"
                    f"建议上调半导体/芯片板块优先级，可推翻盘前‘谨慎/暂停’判定。"
                )
                decision_log.warning(f"⚠️ [MacroRevise] {reason}")
                return True, "strong_acceptance", accept_ratio
            else:
                reason = f"承接力一般: 比值 {accept_ratio:.1%} 大于或等于阈值 {ACCEPT_RATIO_THRESHOLD:.1%}。"
                decision_log.info(f"ℹ️ [MacroRevise] {reason}")
                return False, "normal_acceptance", accept_ratio

        except Exception as e:
            decision_log.error(f"❌ [MacroRevise] 校验外盘承接力异常: {e}")
            return False, f"执行异常: {str(e)}", None

    def check_emotion_deviate(
        self, snapshot: Dict[str, Any], pre_expect: Dict[str, Any], missing_list: List[str], ext_data: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Optional[str], List[Dict[str, Any]]]:
        """
        规则3：半日情绪实时校验
        
        对比指标：半日涨跌比、领涨板块涨幅、实际涨停数。
        判定：任意两项较盘前预判偏离度 > EMOTION_DEVIATE_RATIO (10%)。
        动作：即时修正整体操作模式。
        """
        try:
            # 1. 实际值提取
            actual_ratio = None
            actual_top_change = None
            actual_limit_up = None

            if not snapshot.get("data_missing", True):
                up_num = snapshot.get("half_up_num")
                down_num = snapshot.get("half_down_num")
                if up_num is not None and down_num is not None:
                    actual_ratio = up_num / down_num if down_num > 0 else float(up_num)
                
                actual_top_change = snapshot.get("board_top_change")
                
                # 尝试从快照 ext2 中读取实际涨停家数
                val_ext2 = snapshot.get("ext2")
                if val_ext2:
                    try:
                        actual_limit_up = float(val_ext2)
                    except ValueError:
                        pass

            # 优先采用 ext_data 中的字段进行覆盖
            if ext_data:
                if "actual_ratio" in ext_data:
                    actual_ratio = ext_data["actual_ratio"]
                if "actual_top_change" in ext_data:
                    actual_top_change = ext_data["actual_top_change"]
                if "actual_limit_up" in ext_data:
                    actual_limit_up = ext_data["actual_limit_up"]

            # 2. 预判基准值提取
            expect_ratio = pre_expect.get("up_down_ratio")
            expect_top_change = pre_expect.get("top_board_change")
            expect_limit_up = pre_expect.get("limit_up_num")

            # 3. 逐项计算偏离度
            deviate_items = []
            valid_counts = 0

            # 指标1：半日涨跌比
            if actual_ratio is not None and expect_ratio is not None and expect_ratio > 0:
                dev = (actual_ratio - expect_ratio) / expect_ratio
                deviate_items.append({
                    "metric": "up_down_ratio",
                    "name": "半日涨跌比",
                    "actual": actual_ratio,
                    "expect": expect_ratio,
                    "deviate": dev,
                    "abs_deviate": abs(dev)
                })
                valid_counts += 1
            else:
                missing_list.append("情绪偏离校验: 涨跌比(实际或预判)")

            # 指标2：最强板块涨幅
            if actual_top_change is not None and expect_top_change is not None and expect_top_change > 0:
                dev = (actual_top_change - expect_top_change) / expect_top_change
                deviate_items.append({
                    "metric": "top_board_change",
                    "name": "最强板块涨幅",
                    "actual": actual_top_change,
                    "expect": expect_top_change,
                    "deviate": dev,
                    "abs_deviate": abs(dev)
                })
                valid_counts += 1
            else:
                missing_list.append("情绪偏离校验: 最强板块涨幅(实际或预判)")

            # 指标3：涨停家数
            if actual_limit_up is not None and expect_limit_up is not None and expect_limit_up > 0:
                dev = (actual_limit_up - expect_limit_up) / expect_limit_up
                deviate_items.append({
                    "metric": "limit_up_num",
                    "name": "涨停家数",
                    "actual": actual_limit_up,
                    "expect": expect_limit_up,
                    "deviate": dev,
                    "abs_deviate": abs(dev)
                })
                valid_counts += 1
            else:
                missing_list.append("情绪偏离校验: 实际或预判涨停家数")

            # 如果有效指标少于 2 项，则无法进行“任意两项偏离”的判定，直接跳过
            if valid_counts < 2:
                return False, "有效可对比情绪指标少于2项，跳过校验", deviate_items

            # 4. 判断偏离是否超标
            deviated_list = [item for item in deviate_items if item["abs_deviate"] > EMOTION_DEVIATE_RATIO]

            if len(deviated_list) >= 2:
                # 判定修正方向：根据偏离的平均方向或多数偏离方向决定是上修还是下修
                pos_dev = [item for item in deviated_list if item["deviate"] > 0]
                neg_dev = [item for item in deviated_list if item["deviate"] < 0]
                
                direction = "up" if len(pos_dev) >= len(neg_dev) else "down"
                dir_label = "向上修正" if direction == "up" else "向下修正"
                
                details = ", ".join([f"{i['name']}(偏离:{i['deviate']:.1%})" for i in deviated_list])
                reason = f"触发情绪显著偏离: 共有 {len(deviated_list)} 项情绪指标偏离超 10% ({details})，整体操作模式将进行【{dir_label}】。"
                decision_log.warning(f"⚠️ [MacroRevise] {reason}")
                return True, direction, deviate_items
            else:
                reason = "半日情绪稳定，偏离超 10% 的指标数不足两项。"
                decision_log.info(f"ℹ️ [MacroRevise] {reason}")
                return False, "stable", deviate_items

        except Exception as e:
            decision_log.error(f"❌ [MacroRevise] 情绪偏离校验异常: {e}")
            return False, f"执行异常: {str(e)}", []

    def run(self, pre_result: Dict[str, Any], pre_expect: Dict[str, Any], ext_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        统一修正入口。
        
        参数:
          - pre_result: 盘前综合打分与一票否决汇总结果
          - pre_expect: 盘前预判情绪指标基准字典
          - ext_data: (可选) 额外的半导体板块跌幅与情绪数据，用于补充或覆盖快照
        """
        decision_log.info("🚀 [MacroRevise] 开始盘中实时修正与决策调整程序...")
        
        # 1. 复制盘前原始结果
        original_mode = pre_result.get("operate_mode", "谨慎")
        original_flow = pre_result.get("flow_status", "继续")
        revised_mode = original_mode
        revised_flow = original_flow
        
        revise_items = []
        data_missing_list = []
        board_adjust = "无"
        
        # 2. 查询底层快照及全球宏观数据
        snapshot = macro_query.get_snapshot_1030()
        global_macro = macro_query.get_global_macro()
        
        # 将缺失合并入总缺失清单中
        if snapshot.get("data_missing", True):
            data_missing_list.append("10:30 盘中快照(market_snapshot)")
        if global_macro.get("data_missing", True):
            data_missing_list.append("全球宏观日线(global_macro_daily)")
            
        # 3. 串行执行三大修正规则
        # 规则 1：半日成交额外推
        rule1_triggered, rule1_status, r1_val = self.check_volume_extrapolate(snapshot, data_missing_list)
        revise_items.append({
            "rule_name": "规则1：半日成交额外推校验",
            "status": "触发" if rule1_triggered else "未触发",
            "reason": f"状态: {rule1_status}，缩量比例: {f'{r1_val:.1%}' if r1_val is not None else 'N/A'}"
        })
        
        # 规则 2：外盘承接力
        rule2_triggered, rule2_status, r2_val = self.check_foreign_accept(snapshot, global_macro, data_missing_list, ext_data)
        revise_items.append({
            "rule_name": "规则2：隔夜外盘承接力量化校验",
            "status": "触发" if rule2_triggered else "未触发",
            "reason": f"状态: {rule2_status}，承接比值: {f'{r2_val:.1%}' if r2_val is not None else 'N/A'}"
        })
        
        # 规则 3：情绪偏离
        rule3_triggered, rule3_status, r3_items = self.check_emotion_deviate(snapshot, pre_expect, data_missing_list, ext_data)
        revise_items.append({
            "rule_name": "规则3：半日情绪实时偏离校验",
            "status": "触发" if rule3_triggered else "未触发",
            "reason": f"偏离状态: {rule3_status}，偏离条目数: {len([i for i in r3_items if abs(i['deviate']) > 0.1])}"
        })
        
        # 4. 汇总修正操作模式
        # 缓存前一日模式(如有传入)
        prev_mode = "进攻"
        if ext_data and "prev_operate_mode" in ext_data:
            prev_mode = ext_data["prev_operate_mode"]
            
        # 模式优先级排列为: 进攻 > 谨慎 > 防守
        mode_levels = ["防守", "谨慎", "进攻"]
        
        def mode_level_adjust(curr_m: str, steps: int) -> str:
            """
            辅助调整模式档位
            """
            if curr_m not in mode_levels:
                return curr_m
            idx = mode_levels.index(curr_m)
            new_idx = max(0, min(len(mode_levels) - 1, idx + steps))
            return mode_levels[new_idx]
            
        # 逻辑叠加：
        # A. 规则1：早盘缩量超标 -> 禁止降级为谨慎模式。
        # 如果盘前要降级为“谨慎”（例如前一日是进攻，今天打分下来是谨慎），但早盘严重缩量触发了规则1：
        allow_cautious_downgrade = True
        if rule1_triggered and rule1_status == "severe_shrink":
            allow_cautious_downgrade = False
            # 如果盘前是谨慎，且前一日是进攻，我们限制其降级为谨慎，强制维持前一日模式（进攻）
            if original_mode == "谨慎" and prev_mode == "进攻":
                revised_mode = "进攻"
                decision_log.warning("🔄 [MacroRevise] 触发规则1：禁止直接降级为谨慎，操作模式由 [谨慎] 修正为 [进攻]。")
        
        # B. 规则2：超预期承接力 -> 建议上调该板块优先级，且推翻谨慎/暂停（防守）判定。
        if rule2_triggered and rule2_status == "strong_acceptance":
            board_adjust = "上调半导体/芯片板块优先级（超预期承接）"
            # 推翻谨慎/暂停：如果当前模式是“谨慎”或“防守”，我们直接将其拉满到“进攻”
            if revised_mode in ["谨慎", "防守"]:
                revised_mode = "进攻"
                revised_flow = "继续"
                decision_log.warning(f"🔄 [MacroRevise] 触发规则2：外盘承接力极强，推翻原判定。操作模式由 [{original_mode}] 修正为 [进攻]。")
                
        # C. 规则3：情绪偏离校验
        if rule3_triggered:
            # 情绪极佳（正偏离超标）-> 模式上调一级
            if rule3_status == "up":
                revised_mode = mode_level_adjust(revised_mode, 1)
                decision_log.warning(f"🔄 [MacroRevise] 触发规则3：情绪好于预判，操作模式向上修正一级，当前模式为: [{revised_mode}]。")
            # 情绪崩盘（负偏离超标）-> 模式下调一级
            elif rule3_status == "down":
                # 注意：如果早盘成交额外推禁止降级触发了，而这里又要求下调，我们应当如何取舍？
                # “禁止降级为谨慎模式”是强限制，若下调至“谨慎”则被规则1禁止。
                # 但若下调至“防守”，则不受规则1限制（规则1仅限制降为谨慎）。
                # 这里我们遵循规则1逻辑：如果下调目标是“谨慎”且不允许降级，则跳过此下调，或者直接降为“防守”。
                # 按照“存疑即止”纪律，若情绪极差负偏离，我们倾向于防守，直接降级到“防守”。
                target_mode = mode_level_adjust(revised_mode, -1)
                if target_mode == "谨慎" and not allow_cautious_downgrade:
                    decision_log.warning("🔄 [MacroRevise] 触发规则1与规则3冲突：情绪下修目标为[谨慎]，但被早盘缩量规则禁止降级，维持[进攻]模式。")
                else:
                    revised_mode = target_mode
                    decision_log.warning(f"🔄 [MacroRevise] 触发规则3：情绪差于预判，操作模式向下修正一级，当前模式为: [{revised_mode}]。")

        # 5. 更新仓位上限与流程状态
        if revised_mode == "进攻":
            position_limit = POS_ATTACK
            revised_flow = "继续"
        elif revised_mode == "谨慎":
            position_limit = POS_CAUTIOUS
            revised_flow = "继续"
        else:
            position_limit = POS_DEFEND
            revised_flow = "终止"
            
        decision_log.info(
            f"📊 [MacroRevise] 修正执行完毕。修正后模式: [{revised_mode}]，"
            f"仓位上限: {position_limit:.0%}，决策流向: [{revised_flow}]。"
        )
        
        return {
            "original_mode": original_mode,
            "revised_mode": revised_mode,
            "position_limit": position_limit,
            "revise_items": revise_items,
            "board_adjust": board_adjust,
            "data_missing_list": data_missing_list,
            "flow_status": revised_flow
        }


# 对外提供全局单例修正类对象
macro_revise = MacroRevise()

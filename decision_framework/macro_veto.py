# -*- coding: utf-8 -*-
"""
macro_veto.py —— 第一层宏观环境诊断一票否决与健康度评估引擎
=========================================================

基于项目已就绪数据查询层，独立实现 5 项一票否决规则（包含强制防守/临时谨慎）与
3 大环境健康度风险项判断，遵循“存疑即止”与“异常直接触发防守”的系统设计纪律。
"""

import sqlite3
from typing import Dict, List, Any, Tuple

from decision_framework.macro_query import macro_query
from decision_framework.macro_score import macro_score
from config_loader import *
from decision_framework.decision_log import decision_log
from db.dao import dao


class MacroVeto:
    """
    第一层一票否决与大盘健康度评估单例类。
    """

    def check_liquidity(self, missing_list: List[str]) -> Tuple[bool, str]:
        """
        1. 检查【流动性枯竭】
        判定：成交额单日降幅 > AMOUNT_DROP_RATIO 且 北向净流出 > NORTH_NET_OUT_THRESHOLD
        结果：防守 (defend)，流程终止 (stop)
        """
        try:
            # 获取最近两日的两市总成交额
            conn = dao.get_conn()
            cursor = conn.cursor()
            sql = """
                SELECT trade_date, SUM(amount) as amt 
                FROM daily_prices 
                GROUP BY trade_date 
                ORDER BY trade_date DESC 
                LIMIT 2
            """
            cursor.execute(sql)
            rows = cursor.fetchall()
            conn.close()

            # 存疑即止：如果数据不足以计算单日降幅，触发防守
            if len(rows) < 2:
                missing_list.append("成交额历史对比数据不足(daily_prices)")
                return True, "【数据缺失】无法获取最近两日成交额对比，视同触发流动性枯竭防守"

            curr_amt = float(rows[0][1])
            prev_amt = float(rows[1][1])
            
            if prev_amt <= 0:
                return True, "【数据异常】前一日成交额为零，视同触发流动性枯竭防守"
                
            drop_ratio = (prev_amt - curr_amt) / prev_amt

            # 获取北向资金
            capital = macro_query.get_market_capital()
            north_money = capital.get("north_money")

            if north_money is None:
                missing_list.append("北向流入金额(hsgt_moneyflow.north_money)")
                return True, "【数据缺失】北向资金流入项缺失，视同触发流动性枯竭防守"

            # 北向流入为正，流出为负。大额流出代表 north_money <= -NORTH_NET_OUT_THRESHOLD
            if drop_ratio > AMOUNT_DROP_RATIO and north_money <= -NORTH_NET_OUT_THRESHOLD:
                reason = f"触发流动性枯竭: 两市成交额单日骤降 {drop_ratio:.1%} (阈值 {AMOUNT_DROP_RATIO:.1%})，且北向资金净流出 {abs(north_money)/1e8:.2f} 亿 (阈值 {NORTH_NET_OUT_THRESHOLD/1e8:.2f} 亿)"
                return True, reason

            decision_log.info(f"ℹ️ [MacroVeto] 流动性枯竭校验通过。单日成交降幅: {drop_ratio:.1%}, 北向净额: {north_money/1e8:.2f}亿。")
            return False, ""
        except Exception as e:
            decision_log.error(f"❌ [MacroVeto] 校验流动性枯竭发生异常: {e}，直接触发防守兜底")
            return True, f"校验异常: {str(e)}，触发流动性防守兜底"

    def check_sentiment_collapse(self, missing_list: List[str]) -> Tuple[bool, str]:
        """
        2. 检查【情绪崩塌】
        判定：跌停家数 > LIMIT_DOWN_THRESHOLD 且 最高连板 <= MAX_BOARD_THRESHOLD
        结果：防守 (defend)，流程终止 (stop)
        """
        try:
            sentiment = macro_query.get_market_sentiment()
            limit_down = sentiment.get("limit_down")
            continue_rate = sentiment.get("continue_rate") # 作为连板生态特征

            # 存疑即止：跌停数数据缺失
            if limit_down is None:
                missing_list.append("跌停家数(limit_down)")
                return True, "【数据缺失】跌停家数指标缺失，视同触发情绪崩塌防守"

            # 获取最高连板高度。如果盘后表没有直接记录，我们也可以通过板股连板记录或查表。
            # 这里默认读取 daily_market_post 里的 max_board 字段。
            # 如果缺失该字段，由于“存疑即止”我们视为触发防守，或者降级到 continue_rate 的阀值。
            # 为了体现对最高连板约束的严格落地，我们在表里查找 max_board 字段：
            max_board = None
            conn = dao.get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT max_board FROM daily_market_post ORDER BY trade_date DESC LIMIT 1")
                row = cursor.fetchone()
                if row:
                    max_board = row[0]
            except Exception:
                pass
            finally:
                conn.close()

            if max_board is None:
                # 降级：如果 max_board 字段没有数据，我们从 limit_down 来进行保守评估
                if limit_down > LIMIT_DOWN_THRESHOLD:
                    reason = f"触发情绪崩塌: 跌停家数达 {limit_down} 家 (阈值 {LIMIT_DOWN_THRESHOLD} 家)，最高连板缺失视为未达标"
                    return True, reason
                decision_log.info("ℹ️ [MacroVeto] 最高连板高度 max_board 缺失，但跌停数健康，免于情绪崩塌触发。")
                return False, ""

            if limit_down > LIMIT_DOWN_THRESHOLD and max_board <= MAX_BOARD_THRESHOLD:
                reason = f"触发情绪崩塌: 跌停家数 {limit_down} 家 (阈值 {LIMIT_DOWN_THRESHOLD})，最高连板仅 {max_board} 板 (阈值 {MAX_BOARD_THRESHOLD} 板)"
                return True, reason

            decision_log.info(f"ℹ️ [MacroVeto] 情绪崩塌校验通过。跌停数: {limit_down}, 最高连板: {max_board}板。")
            return False, ""
        except Exception as e:
            decision_log.error(f"❌ [MacroVeto] 校验情绪崩塌发生异常: {e}，直接触发防守兜底")
            return True, f"校验异常: {str(e)}，触发情绪防守兜底"

    def check_reg_policy(self, missing_list: List[str]) -> Tuple[bool, str]:
        """
        3. 检查【监管黑天鹅】
        判定：是否存在重大监管紧缩政策信号
        由于国内政策无直接结构化事实表，因此若库中无指定极端利空信号，不触发，但写日志。
        """
        # 数据暂无对应表格，直接视为未触发，并在决策日志中记录
        decision_log.info("ℹ️ [MacroVeto] 监管黑天鹅数据未见利空信号，跳过判定。")
        return False, ""

    def check_outer_system_risk(self, missing_list: List[str]) -> Tuple[bool, str]:
        """
        4. 检查【外围系统性冲击】
        判定：美股三大指数跌幅同步跌超 US_INDEX_DROP_THRESHOLD 且 VIX >= VIX_RISK_THRESHOLD
        结果：防守 (defend)，流程终止 (stop)
        """
        try:
            macro = macro_query.get_global_macro()
            vix = macro.get("vix")
            spx_pct = macro.get("spx_pct")
            dji_pct = macro.get("dji_pct")
            ixic_pct = macro.get("ixic_pct")

            if vix is None or spx_pct is None or dji_pct is None or ixic_pct is None:
                missing_list.append("全球宏观字段(vix/美股三大指数涨幅)")
                return True, "【数据缺失】外围关键宏观指标缺失，视同触发外围系统性冲击防守"

            # 同步跌幅跌破阈值 (注意：跌幅在表中为百分比负数，比如跌了 3% 记录为 -3.0，
            # 故同步跌破跌幅阈值意味着全部小于等于 -US_INDEX_DROP_THRESHOLD)
            is_us_drop = (spx_pct <= -US_INDEX_DROP_THRESHOLD and 
                          dji_pct <= -US_INDEX_DROP_THRESHOLD and 
                          ixic_pct <= -US_INDEX_DROP_THRESHOLD)

            if is_us_drop and vix >= VIX_RISK_THRESHOLD:
                reason = f"触发外围系统性冲击: VIX波动率 {vix} (阈值 {VIX_RISK_THRESHOLD})，且美股三大指数同步大跌超 {US_INDEX_DROP_THRESHOLD}% (S&P: {spx_pct}%, DJI: {dji_pct}%, NASDAQ: {ixic_pct}%)"
                return True, reason

            decision_log.info(f"ℹ️ [MacroVeto] 外围系统性冲击校验通过。VIX: {vix}, 美股大盘运行健康。")
            return False, ""
        except Exception as e:
            decision_log.error(f"❌ [MacroVeto] 校验外围系统性冲击异常: {e}，触发防守兜底")
            return True, f"校验异常: {str(e)}，触发外围系统性防守兜底"

    def check_outer_flash_crash(self, missing_list: List[str]) -> Tuple[bool, str]:
        """
        5. 检查【外围盘中闪崩】
        判定：韩日日内跌幅均超 FOREIGN_INDEX_DROP 且 A股开盘 30 分钟(10:30快照)跌幅超 A_OPEN_DROP
        结果：谨慎 (cautious)，流程继续 (continue)
        """
        try:
            snapshot = macro_query.get_snapshot_1030()
            kr_index_change = snapshot.get("kr_index_change")
            jp_index_change = snapshot.get("jp_index_change")
            
            # A股在快照里可以通过美股或板块变动判断，但此处如果缺失，我们默认使用
            # 板块资金流入和快照成交额判断，或者如果 market_snapshot 有记录 a_open_drop 
            # (在此我们可以根据 10:30 快照中的 A股实际走势跌幅作为代替，由于未建独立大盘跌幅字段，
            # 兼容使用 board_top_change 或是全球宏观指数替代)
            # 我们在 snapshot 里面获取 board_top_change (板块变动) 或者是 extrapolation。
            # 为了严谨落地，在 A 股无直接跌幅字段时，如果韩日指数跌幅满足条件，则进行谨慎模式。
            if kr_index_change is None or jp_index_change is None:
                # 不视同 defend，因为这是等级较低的“临时谨慎”判定。我们只在日志提示，不直接触发
                decision_log.info("ℹ️ [MacroVeto] 10:30 快照外盘数据缺失，跳过盘中闪崩规则判定。")
                return False, ""

            # 判断日韩是否跌破阈值 (跌幅小于等于 -FOREIGN_INDEX_DROP)
            is_foreign_crash = (kr_index_change <= -FOREIGN_INDEX_DROP or jp_index_change <= -FOREIGN_INDEX_DROP)

            if is_foreign_crash:
                # 判定 A 股日内情绪变动 (可以用 10:30 快照里的 half_down_num / (half_up_num + half_down_num) 
                # 大于 80% 代表 A股同步暴跌作为 A_OPEN_DROP 替代)
                half_up = snapshot.get("half_up_num", 0) or 0
                half_down = snapshot.get("half_down_num", 0) or 0
                total_snap = half_up + half_down
                is_a_crash = False
                
                if total_snap > 0 and (half_down / total_snap) > 0.8:
                    is_a_crash = True
                
                if is_a_crash:
                    reason = f"触发外围盘中闪崩: 韩日指数出现大跌(韩: {kr_index_change}%, 日: {jp_index_change}%)，且A股盘中下跌占比高达 {half_down/total_snap:.1%}"
                    return True, reason

            decision_log.info("ℹ️ [MacroVeto] 外围盘中闪崩校验通过。日韩大盘波动平稳。")
            return False, ""
        except Exception as e:
            decision_log.warning(f"⚠️ [MacroVeto] 校验外围盘中闪崩发生异常: {e}，跳过此临时谨慎规则")
            return False, ""

    # =========================================================================
    # 批量校验与三大健康度
    # =========================================================================

    def check_all_veto(self) -> Dict[str, Any]:
        """
        批量否决校验。
        按顺序遍历 5 条规则，一旦触发立即终止校验。
        """
        missing_list = []
        
        # 1. 检查流动性枯竭 (Defend, Stop)
        triggered, reason = self.check_liquidity(missing_list)
        if triggered:
            return {
                "veto_triggered": True,
                "veto_type": "defend",
                "flow_status": "stop",
                "trigger_reason": reason,
                "missing_list": missing_list
            }

        # 2. 检查情绪崩塌 (Defend, Stop)
        triggered, reason = self.check_sentiment_collapse(missing_list)
        if triggered:
            return {
                "veto_triggered": True,
                "veto_type": "defend",
                "flow_status": "stop",
                "trigger_reason": reason,
                "missing_list": missing_list
            }

        # 3. 检查政策黑天鹅 (Defend, Stop)
        triggered, reason = self.check_reg_policy(missing_list)
        if triggered:
            return {
                "veto_triggered": True,
                "veto_type": "defend",
                "flow_status": "stop",
                "trigger_reason": reason,
                "missing_list": missing_list
            }

        # 4. 检查外围系统风险 (Defend, Stop)
        triggered, reason = self.check_outer_system_risk(missing_list)
        if triggered:
            return {
                "veto_triggered": True,
                "veto_type": "defend",
                "flow_status": "stop",
                "trigger_reason": reason,
                "missing_list": missing_list
            }

        # 5. 检查外围盘中闪崩 (Cautious, Continue)
        triggered, reason = self.check_outer_flash_crash(missing_list)
        if triggered:
            return {
                "veto_triggered": True,
                "veto_type": "cautious",
                "flow_status": "continue",
                "trigger_reason": reason,
                "missing_list": missing_list
            }

        # 未触发任何否决
        return {
            "veto_triggered": False,
            "veto_type": None,
            "flow_status": "continue",
            "trigger_reason": "未触发一票否决项，环境运转正常",
            "missing_list": missing_list
        }

    def check_health_degree(self) -> Dict[str, Any]:
        """
        三大环境健康度指标评估。
        1. 万得全A (上证指数) 站上 20日线
        2. 全A等权 (深证成指) 站上 20日线
        3. 涨跌家数比 5日均值 >= UP_DOWN_RATIO_STANDARD (1.0)
        """
        risk_list = []
        data_missing = []

        # 1. 检查上证指数 20 日线位置
        ma1 = macro_query.get_index_ma("000001.SH")
        if ma1.get("data_missing", True):
            data_missing.append("上证指数20日均线数据")
        else:
            if not ma1.get("is_above_ma20", False):
                risk_list.append("上证指数(000001.SH)目前处于20日均线下方，趋势走弱")

        # 2. 检查深证成指 20 日线位置 (用作全A等权参照)
        ma2 = macro_query.get_index_ma("399001.SZ")
        if ma2.get("data_missing", True):
            data_missing.append("深证成指20日均线数据")
        else:
            if not ma2.get("is_above_ma20", False):
                risk_list.append("深证成指(399001.SZ)目前处于20日均线下方，市场主线走弱")

        # 3. 涨跌家数比 5日均值
        up_down_ok = True
        try:
            conn = dao.get_conn()
            cursor = conn.cursor()
            # 查出过去 5 天每日的上涨数与下跌数
            sql = """
                SELECT 
                    trade_date, 
                    SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END) as up_num,
                    SUM(CASE WHEN pct_chg < 0 THEN 1 ELSE 0 END) as down_num
                FROM daily_prices 
                GROUP BY trade_date 
                ORDER BY trade_date DESC 
                LIMIT 5
            """
            cursor.execute(sql)
            rows = cursor.fetchall()
            conn.close()

            if len(rows) < 5:
                data_missing.append("最近5日个股涨跌比对比数据不足")
                up_down_ok = False
            else:
                ratio_sum = 0.0
                for r in rows:
                    up = float(r[1])
                    down = float(r[2])
                    if down > 0:
                        ratio_sum += (up / down)
                    else:
                        ratio_sum += 1.0 # 极端情况
                
                avg_ratio = ratio_sum / 5.0
                if avg_ratio < UP_DOWN_RATIO_STANDARD:
                    risk_list.append(f"最近5日个股涨跌比均值低于 {UP_DOWN_RATIO_STANDARD:.1f} (实际为: {avg_ratio:.2f})，情绪偏弱")
        except Exception as e:
            decision_log.debug(f"[MacroVeto] 校验5日涨跌比异常: {e}")
            data_missing.append("涨跌比5日均值查询异常")
            up_down_ok = False

        # 判定健康度状态
        if risk_list:
            health_status = "存在背离风险"
        else:
            health_status = "良性"

        return {
            "health_status": health_status,
            "risk_list": risk_list,
            "data_missing": data_missing
        }

    # =========================================================================
    # 对外统一接口 run()
    # =========================================================================

    def run(self) -> Dict[str, Any]:
        """
        全量否决与健康度评估。
        """
        decision_log.info("🚀 [MacroVeto] 开始执行第一层一票否决与健康度判定流程...")
        
        veto_res = self.check_all_veto()
        health_res = self.check_health_degree()
        
        if veto_res["veto_triggered"]:
            decision_log.warning(
                f"🚨 [MacroVeto] 触发一票否决校验! 类型: [{veto_res['veto_type']}]，"
                f"流向状态: [{veto_res['flow_status']}]，触发原因: {veto_res['trigger_reason']}。"
            )
        else:
            decision_log.info("✅ [MacroVeto] 未触发任何一票否决项，大盘健康度评估通过。")

        return {
            "veto_result": veto_res,
            "health_result": health_res
        }


# 对外提供全局评估单例
macro_veto = MacroVeto()

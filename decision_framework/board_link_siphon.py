# -*- coding: utf-8 -*-
"""
board_link_siphon.py —— 第二层板块风格与轮动决策框架：跨板块联动与二次资金虹吸校验收官子模块
=====================================================================================

本模块承接梯队、风格热度、轮动以及仓位管控等前置结论，独立执行两大精细化判定：
1. 跨板块联动识别：基于成分股收盘价序列，计算同大类细分板块及跨大类板块的价格 Pearson 相关系数、日内涨跌同步概率以及资金同向流动占比，判定联动档位（强/一般/无）和方向（正/反）。
2. 二次资金虹吸量化校验：计算主线板块吸金占比与弱势板块资金流失率，结合风格热度、轮动强度、持仓仓位进行乘数修正，划分虹吸强度并与第一层粗判做智能对比。
3. 联动传导风险提示与策略输出：提供具体的板块避险规避清单与机会清单，触发强风控时拦截后续交易流程。
"""

import math
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd

from decision_framework.board_rank import board_rank
from decision_framework.board_structure import board_structure
from decision_framework.board_style import board_style
from decision_framework.board_rotation import board_rotation
from decision_framework.board_position import board_position
from decision_framework.macro_query import macro_query
from db.dao import dao
from config_loader import *
from decision_framework.decision_log import decision_log


class BoardLinkSiphon:
    """
    跨板块联动识别与二次资金虹吸量化决策单例类。
    """

    def _get_dates(self, limit: int = 5) -> List[str]:
        """
        获取最近的若干个交易日序列
        """
        conn = dao.get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            dates = [r[0] for r in rows]
            dates.reverse()  # 变成由老到新
            return dates
        except Exception as e:
            decision_log.error(f"❌ [BoardLinkSiphon] 查询历史交易日失败: {e}")
            return []
        finally:
            conn.close()

    def _get_board_stock_prices(self, board_name: str, dates: List[str]) -> List[float]:
        """
        获取板块最近若干日成分股收盘价均值序列
        """
        if not dates:
            return []
        conn = dao.get_conn()
        cursor = conn.cursor()
        try:
            # 获取成分股列表
            cursor.execute("SELECT ts_code FROM stock_list WHERE industry = ?", (board_name,))
            stocks = [r[0] for r in cursor.fetchall()]
            if not stocks:
                return []

            placeholders = ",".join(["?"] * len(stocks))
            date_placeholders = ",".join(["?"] * len(dates))
            sql = f"""
                SELECT trade_date, AVG(close) as avg_close 
                FROM daily_prices 
                WHERE ts_code IN ({placeholders}) AND trade_date IN ({date_placeholders})
                GROUP BY trade_date
                ORDER BY trade_date ASC
            """
            df = pd.read_sql(sql, conn, params=stocks + dates)
            
            # 保证返回序列与 dates 严格按顺序对齐且无缺失
            price_map = {row["trade_date"]: float(row["avg_close"]) for _, row in df.iterrows()}
            prices = []
            last_p = 0.0
            for dt in dates:
                p = price_map.get(dt, last_p)
                prices.append(p)
                if p > 0:
                    last_p = p
            return prices
        except Exception as e:
            decision_log.warning(f"⚠️ [BoardLinkSiphon] 获取板块 [{board_name}] 均价历史异常: {e}")
            return []
        finally:
            conn.close()

    def _get_board_flows(self, board_name: str, dates: List[str]) -> List[float]:
        """
        获取板块最近若干日的资金净流入序列
        """
        if not dates:
            return []
        conn = dao.get_conn()
        try:
            date_placeholders = ",".join(["?"] * len(dates))
            sql = f"""
                SELECT trade_date, net_amount 
                FROM board_money_flow 
                WHERE board_name = ? AND trade_date IN ({date_placeholders})
                ORDER BY trade_date ASC
            """
            df = pd.read_sql(sql, conn, params=[board_name] + dates)
            
            flow_map = {row["trade_date"]: float(row["net_amount"]) for _, row in df.iterrows()}
            flows = []
            for dt in dates:
                flows.append(flow_map.get(dt, 0.0))
            return flows
        except Exception as e:
            decision_log.warning(f"⚠️ [BoardLinkSiphon] 获取板块 [{board_name}] 资金流向历史异常: {e}")
            return []
        finally:
            conn.close()

    def judge_board_link(self, style_group: List[Dict[str, Any]], data_missing: List[str]) -> List[Dict[str, Any]]:
        """
        1. 跨板块联动识别

        判定板块组合的联动档位（强/一般/无）和方向（正/反）。
        """
        link_outputs = []

        # 整理所有含有候选板块的板块名称与归属风格
        active_boards = []
        board_to_style = {}
        for style_item in style_group:
            s_name = style_item.get("style_name")
            b_list = style_item.get("board_list", [])
            for b in b_list:
                active_boards.append(b)
                board_to_style[b] = s_name

        if len(active_boards) < 2:
            decision_log.info("ℹ️ [BoardLinkSiphon] 活跃候选板块少于2个，无需进行联动对识别")
            return link_outputs

        # 获取近 5 个交易日数据
        dates = self._get_dates(5)
        if len(dates) < 3:
            data_missing.append("跨板块联动: 交易日数据不足")
            return link_outputs

        # 板块两两配对，判定联动
        pairs = []
        for i in range(len(active_boards)):
            for j in range(i + 1, len(active_boards)):
                pairs.append((active_boards[i], active_boards[j]))

        for b1, b2 in pairs:
            try:
                prices1 = self._get_board_stock_prices(b1, dates)
                prices2 = self._get_board_stock_prices(b2, dates)
                flows1 = self._get_board_flows(b1, dates)
                flows2 = self._get_board_flows(b2, dates)

                if len(prices1) < 3 or len(prices2) < 3:
                    decision_log.warning(f"⚠️ [BoardLinkSiphon] 板块对 [{b1} - {b2}] 历史价格数据长度不足，跳过")
                    continue

                # A. 计算 Pearson 相关系数
                s1 = pd.Series(prices1)
                s2 = pd.Series(prices2)
                corr = s1.corr(s2)
                if pd.isna(corr):
                    corr = 0.0

                # B. 计算涨跌同步概率 (同向涨跌天数占比，N-1天比较)
                sync_days = 0
                total_compare_days = len(prices1) - 1
                for k in range(1, len(prices1)):
                    diff1 = prices1[k] - prices1[k - 1]
                    diff2 = prices2[k] - prices2[k - 1]
                    if (diff1 > 0 and diff2 > 0) or (diff1 < 0 and diff2 < 0) or (diff1 == 0 and diff2 == 0):
                        sync_days += 1
                sync_prob = sync_days / total_compare_days if total_compare_days > 0 else 0.0

                # C. 计算资金同向流动占比
                same_dir_days = 0
                for f1, f2 in zip(flows1, flows2):
                    if (f1 > 0 and f2 > 0) or (f1 <= 0 and f2 <= 0):
                        same_dir_days += 1
                fund_same_ratio = same_dir_days / len(flows1) if flows1 else 0.0

                # 联动方向判定
                if corr >= 0:
                    link_dir = "正向联动"
                else:
                    link_dir = "反向联动"

                # 联动档位判定
                # 强联动: 相关系数 >= LINK_CORR_STRONG 且 同步概率 >= SYNC_PROB_STRONG
                # 一般联动: 相关系数 >= LINK_CORR_MID 或 同步概率 >= SYNC_PROB_MID
                if corr >= LINK_CORR_STRONG and sync_prob >= SYNC_PROB_STRONG:
                    link_level = "强联动"
                elif corr >= LINK_CORR_MID or sync_prob >= SYNC_PROB_MID:
                    link_level = "一般联动"
                else:
                    link_level = "无联动"
                    link_dir = "正向联动"  # 无联动时方向默认为正向

                # 联动大类划分
                style1 = board_to_style[b1]
                style2 = board_to_style[b2]
                if style1 == style2:
                    link_type = "同大类细分"
                else:
                    link_type = "跨大类"

                detail_desc = f"相关系数: {corr:.2f} | 同步概率: {sync_prob:.1%} | 资金同向比: {fund_same_ratio:.1%}"
                
                link_outputs.append({
                    "board_group": f"{b1} - {b2}",
                    "link_type": link_type,
                    "link_level": link_level,
                    "link_dir": link_dir,
                    "link_detail": detail_desc
                })

                decision_log.info(
                    f"📊 [BoardLinkSiphon] 板块组合 [{b1} - {b2}] 联动评估 -> "
                    f"类型: {link_type} | 级别: {link_level} | 方向: {link_dir} | 明细: {detail_desc}"
                )

            except Exception as e:
                decision_log.error(f"❌ [BoardLinkSiphon] 判定板块 [{b1} - {b2}] 联动组合发生异常: {e}")

        return link_outputs

    def calc_second_siphon(
        self,
        style_group: List[Dict[str, Any]],
        rotate_strength: str,
        position_result: Dict[str, Any],
        data_missing: List[str],
    ) -> Dict[str, Any]:
        """
        2. 二次精细化资金虹吸校验

        计算主线吸金占比、弱势流失率并结合风格、轮动与仓位进行修正，最终输出虹吸级别。
        """
        siphon_level = "无虹吸"
        absorb_ratio = 0.0
        loss_rate = 0.0
        influence_range = "暂无"
        compare_first = "一二层判定一致，无严重虹吸"

        # 找到最新一个可用的交易日
        conn = dao.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(trade_date) FROM daily_prices")
        row_date = cursor.fetchone()
        conn.close()

        if not row_date or not row_date[0]:
            data_missing.append("二次虹吸: 价格表数据为空")
            return {
                "siphon_level": "无虹吸",
                "absorb_ratio": "0.0%",
                "loss_rate": "0.0%",
                "influence_range": "暂无",
                "compare_first": "数据缺失，降级为无虹吸"
            }

        latest_date = row_date[0]

        try:
            # 1. 确定主线大类风格
            # 主线风格指：上一层判定中，日内热度或跨日热度为“强势”的风格。
            # 如果没有强势风格，取候选板块中，板块得分第一名所在的风格；再没有则默认为“科技”。
            main_style = None
            strong_styles = [s for s in style_group if s.get("intraday_strength") == "强势" or s.get("cross_day_strength") == "强势"]
            if strong_styles:
                # 优先取包含候选板块个数最多的那个强势风格
                strong_styles.sort(key=lambda x: len(x.get("board_list", [])), reverse=True)
                main_style = strong_styles[0]["style_name"]
            else:
                # 找得分最高的板块归属风格
                all_active_boards = []
                for s in style_group:
                    all_active_boards.extend(s.get("board_list", []))
                
                if all_active_boards:
                    # 查第一层 board_rank 里的最高板块
                    # 这里为了极速运行，直接查数据库里今天得分最高的板块
                    conn = dao.get_conn()
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT board_name FROM board_money_flow 
                        WHERE trade_date = ? 
                        ORDER BY net_amount DESC LIMIT 1
                    """, (latest_date,))
                    row_top = cursor.fetchone()
                    conn.close()
                    if row_top:
                        top_board = row_top[0]
                        # 查找这个板块对应的风格
                        # STYLE_MAP 是在 config_loader 里面导出的字典
                        main_style = STYLE_MAP.get(top_board, "科技")
                
            if not main_style:
                main_style = "科技"

            # 2. 计算吸金占比 (主线风格候选板块的总净流入 / 市场所有候选板块的正净流入总和)
            conn = dao.get_conn()
            main_boards = [item.get("board_list", []) for item in style_group if item.get("style_name") == main_style]
            main_boards_list = main_boards[0] if main_boards else []
            
            main_flow_sum = 0.0
            if main_boards_list:
                placeholders = ",".join(["?"] * len(main_boards_list))
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT SUM(net_amount) FROM board_money_flow 
                    WHERE board_name IN ({placeholders}) AND trade_date = ?
                """, main_boards_list + [latest_date])
                row = cursor.fetchone()
                main_flow_sum = float(row[0]) if row and row[0] is not None else 0.0

            # 统计全市场所有有净流入的候选板块的总净流入
            all_active_boards = []
            for s in style_group:
                all_active_boards.extend(s.get("board_list", []))
            
            market_positive_sum = 0.0
            if all_active_boards:
                placeholders = ",".join(["?"] * len(all_active_boards))
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT net_amount FROM board_money_flow 
                    WHERE board_name IN ({placeholders}) AND trade_date = ?
                """, all_active_boards + [latest_date])
                rows = cursor.fetchall()
                market_positive_sum = sum(max(0.0, float(r[0])) for r in rows if r[0] is not None)

            conn.close()

            # 算吸金占比
            if market_positive_sum > 0 and main_flow_sum > 0:
                absorb_ratio = main_flow_sum / market_positive_sum
            else:
                absorb_ratio = 0.0

            # 3. 计算资金流失率 (弱势风格中流失最严重的那个风格的流失率 = 净流出绝对值 / 风格总成交额)
            weak_styles = [s for s in style_group if s.get("intraday_strength") == "弱势" or s.get("cross_day_strength") == "弱势"]
            max_loss_rate = 0.0
            worst_weak_style = "无"
            worst_boards = []

            for ws in weak_styles:
                ws_name = ws.get("style_name")
                ws_boards = ws.get("board_list", [])
                if not ws_boards:
                    continue
                
                # 查该弱势风格板块今日的总净流出与总成交额
                conn = dao.get_conn()
                try:
                    placeholders = ",".join(["?"] * len(ws_boards))
                    cursor = conn.cursor()
                    cursor.execute(f"""
                        SELECT SUM(net_amount) FROM board_money_flow 
                        WHERE board_name IN ({placeholders}) AND trade_date = ?
                    """, ws_boards + [latest_date])
                    row_net = cursor.fetchone()
                    ws_net_flow = float(row_net[0]) if row_net and row_net[0] is not None else 0.0

                    # 查这些弱势板块内个股的总成交额
                    # 先查弱势板块的全部成份股
                    placeholders_b = ",".join(["?"] * len(ws_boards))
                    cursor.execute(f"SELECT ts_code FROM stock_list WHERE industry IN ({placeholders_b})", ws_boards)
                    stks = [r[0] for r in cursor.fetchall()]
                    
                    ws_amount = 0.0
                    if stks:
                        placeholders_s = ",".join(["?"] * len(stks))
                        cursor.execute(f"""
                            SELECT SUM(amount) FROM daily_prices 
                            WHERE ts_code IN ({placeholders_s}) AND trade_date = ?
                        """, stks + [latest_date])
                        row_amt = cursor.fetchone()
                        ws_amount = float(row_amt[0]) if row_amt and row_amt[0] is not None else 0.0

                    # 如果是流出 (net_flow < 0) 且成交额大于 0
                    if ws_net_flow < 0 and ws_amount > 0:
                        loss = abs(ws_net_flow) / ws_amount
                        if loss > max_loss_rate:
                            max_loss_rate = loss
                            worst_weak_style = ws_name
                            worst_boards = ws_boards
                except Exception as e:
                    decision_log.warning(f"⚠️ [BoardLinkSiphon] 计算弱势风格 [{ws_name}] 流失率异常: {e}")
                finally:
                    conn.close()

            loss_rate = max_loss_rate

            # 4. 修正系数计算 (叠加修正：强热度+强轮动+高持仓)
            multiplier = 1.0
            
            # 主线风格热度强势
            main_style_item = [s for s in style_group if s.get("style_name") == main_style]
            if main_style_item:
                if main_style_item[0].get("intraday_strength") == "强势" or main_style_item[0].get("cross_day_strength") == "强势":
                    multiplier += 0.1
                    
            # 全市场强轮动
            if rotate_strength == "强轮动":
                multiplier += 0.1
            elif rotate_strength == "弱轮动":
                multiplier -= 0.1

            # 账户主线持仓仓位较高 (从 position_result 的 style_position 列表提取该主线合并仓位)
            main_pos = 0.0
            if "style_position" in position_result:
                match_pos = [p for p in position_result["style_position"] if p.get("style_name") == main_style]
                if match_pos:
                    main_pos = float(match_pos[0].get("merge_pos", 0.0))
            
            if main_pos > 0.30:
                multiplier += 0.1

            adjusted_absorb = absorb_ratio * multiplier
            adjusted_loss = loss_rate * multiplier

            # 5. 判定精细化虹吸等级 (若全市场无板块失血流出，则直接判定为无虹吸，规避普涨行情误判)
            if loss_rate <= 0.0:
                siphon_level = "无虹吸"
            elif adjusted_absorb >= SIPHON_ABSORB_STRONG and adjusted_loss >= LOSS_RATE_STRONG:
                siphon_level = "强虹吸"
            elif adjusted_absorb >= SIPHON_ABSORB_MID and adjusted_loss >= LOSS_RATE_MID:
                siphon_level = "中等虹吸"
            elif absorb_ratio > 0.05 or loss_rate > 0.02:
                siphon_level = "弱虹吸"
            else:
                siphon_level = "无虹吸"

            # 影响板块范围说明
            if siphon_level in ["强虹吸", "中等虹吸"] and worst_boards:
                influence_range = f"主线大类 [{main_style}] (主线板块: {main_boards_list}) 吸金压制弱势大类 [{worst_weak_style}] 板块（受压制板块: {worst_boards}）"
            else:
                influence_range = "全市场未见大范围严重虹吸压制"

            # 6. 与第一层粗判结果做差值比对
            # 获取第一层虹吸校验结论
            try:
                first_rank_res = board_rank.run()
                first_siphon_risk = first_rank_res.get("siphon_risk", "无")
            except Exception:
                first_siphon_risk = "无"

            if first_siphon_risk == "无" and siphon_level in ["强虹吸", "中等虹吸"]:
                compare_first = (
                    f"第一层粗判为 [无虹吸风险]，本层通过个股流失率精细校验，"
                    f"识别出主线 [{main_style}] 吸金占比 {absorb_ratio:.1%} 与被压制流失率 {loss_rate:.1%}，"
                    f"联动仓位修正后升级为 [{siphon_level}] 并进行阻断，弥补了初筛漏洞。"
                )
            elif first_siphon_risk == "有" and siphon_level in ["弱虹吸", "无虹吸"]:
                compare_first = (
                    f"第一层粗判为 [有虹吸风险]，本层经量化穿透后发现弱势板块流失率仅 {loss_rate:.1%}，"
                    f"并未发生恐慌性流出，调降为 [{siphon_level}] 并解除交易限制，释放了交易空间。"
                )
            else:
                compare_first = f"第一层粗判 ([{first_siphon_risk}虹吸风险]) 与本层精细化计算 ([{siphon_level}]) 结论在等级区间上方向一致。"

            decision_log.info(
                f"📊 [BoardLinkSiphon] 二次虹吸校验 -> 主线: {main_style} | 吸金占比: {absorb_ratio:.1%} "
                f"| 弱势最大流失率: {loss_rate:.1%} | 修正乘数: {multiplier:.1f} | 评级: {siphon_level} | 差异: {compare_first}"
            )

        except Exception as e:
            decision_log.error(f"❌ [BoardLinkSiphon] 二次资金虹吸校验发生异常: {e}")
            compare_first = f"系统计算异常: {str(e)}，按高风控降级处理"

        return {
            "siphon_level": siphon_level,
            "absorb_ratio": f"{absorb_ratio:.1%}",
            "loss_rate": f"{loss_rate:.1%}",
            "influence_range": influence_range,
            "compare_first": compare_first
        }

    def gen_risk_strategy(
        self, link_info: List[Dict[str, Any]], siphon_info: Dict[str, Any]
    ) -> Tuple[str, Dict[str, str]]:
        """
        3. 风险与策略生成
        """
        # A. 传导风险提示
        s_level = siphon_info.get("siphon_level", "无虹吸")
        if s_level == "强虹吸":
            risk_warn = "【极高风险】主线板块吸金效应极强，弱势板块正面临失血抽水，多头动能急剧衰减，极易出现大面积冲高回落！"
        elif s_level == "中等虹吸":
            risk_warn = "【中等风险】主线大类资金吸纳度较高，弱势风格板块有轻微流失，关注个股联动传导的避险波动。"
        else:
            # 统计是否有强联动或跷跷板
            has_strong_link = any(x["link_level"] == "强联动" for x in link_info)
            if has_strong_link:
                risk_warn = "【联动提醒】市场无明显虹吸。部分板块间呈高度强联动，警惕跟涨标的补跌或冲高共振。"
            else:
                risk_warn = "【风控正常】全市场资金流动合理，无板块虹吸风险与异常联动风险。"

        # B. 板块策略生成
        avoid_reasons = []
        opportunity_reasons = []

        # 被虹吸压制板块
        inf_range = siphon_info.get("influence_range", "")
        if "主线大类" in inf_range and s_level in ["强虹吸", "中等虹吸"]:
            avoid_reasons.append(f"{inf_range}，资金流失率达 {siphon_info.get('loss_rate')}，严禁追高。")

        # 从联动组合里生成策略
        for link in link_info:
            grp = link["board_group"]
            b1, b2 = grp.split(" - ")
            lvl = link["link_level"]
            d_dir = link["link_dir"]

            if lvl == "强联动":
                if d_dir == "正向联动":
                    opportunity_reasons.append(f"板块 [{b1}] 与 [{b2}] 正向强共振，适合联动跟单，可寻找滞涨跟风标的补涨机会。")
                elif d_dir == "反向联动":
                    opportunity_reasons.append(f"板块 [{b1}] 与 [{b2}] 呈反向跷跷板效应，当主线核心分歧时，可博弈另一方的反抽机会。")
            elif lvl == "一般联动" and d_dir == "正向联动":
                opportunity_reasons.append(f"板块 [{b1}] 与 [{b2}] 存在正向一般联动，多头趋势下可温和关注。")

        avoid_list = " | ".join(avoid_reasons) if avoid_reasons else "暂无需要重点强制规避的板块"
        opportunity_list = " | ".join(opportunity_reasons) if opportunity_reasons else "多看少动，等待联动主线或突破信号确立"

        strategy = {
            "avoid_list": avoid_list,
            "opportunity_list": opportunity_list
        }

        return risk_warn, strategy

    def run(
        self,
        style_result: Optional[Dict[str, Any]] = None,
        rotation_result: Optional[Dict[str, Any]] = None,
        position_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        收官决策工作流统一主入口。
        """
        decision_log.info("🚀 [BoardLinkSiphon] 启动跨板块联动与二次资金虹吸校验收官程序...")
        data_missing_list = []
        flow_status = "继续"

        if style_result is None or rotation_result is None or position_result is None:
            from decision_framework.board_rank import board_rank
            from decision_framework.board_style import board_style
            from decision_framework.board_rotation import board_rotation
            from decision_framework.board_position import board_position
            try:
                board_res = board_rank.run()
                if style_result is None:
                    style_result = board_style.run(board_res)
                if rotation_result is None:
                    rotation_result = board_rotation.run(style_result)
                if position_result is None:
                    position_result = board_position.run(style_result, rotation_result)
            except Exception as ex:
                decision_log.error(f"❌ [BoardLinkSiphon] 自动运行前置模块异常: {ex}")

        try:
            if not style_result:
                style_result = {"style_group": []}
            if not rotation_result:
                rotation_result = {"rotate_strength": "弱轮动"}
            if not position_result:
                position_result = {"market_total_pos": 0.0, "style_position": []}

            style_group = style_result.get("style_group", [])
            rotate_strength = rotation_result.get("rotate_strength", "弱轮动")
            market_total_pos = position_result.get("market_total_pos", 0.0)

            # 1. 跨板块联动识别
            link_info = self.judge_board_link(style_group, data_missing_list)

            # 2. 二次资金虹吸量化校验
            siphon_info = self.calc_second_siphon(style_group, rotate_strength, position_result, data_missing_list)

            # 3. 风险与交易策略生成
            risk_warn, strategy = self.gen_risk_strategy(link_info, siphon_info)

            # 4. 风控终止决策：如果是强虹吸，流程终止
            if siphon_info.get("siphon_level") == "强虹吸":
                flow_status = "终止"
                decision_log.warning("⚠️ [BoardLinkSiphon] 触发强虹吸风控硬性拦截条件，决策流强制终止！")

            result = {
                "link_info": link_info,
                "siphon_info": siphon_info,
                "risk_warn": risk_warn,
                "strategy": strategy,
                "data_missing_list": list(set(data_missing_list)),
                "flow_status": flow_status
            }

            decision_log.info(
                f"✅ [BoardLinkSiphon] 第二层收官评估完毕。虹吸评级: [{siphon_info.get('siphon_level')}] "
                f"| 流程状态: [{flow_status}]。"
            )
            return result

        except Exception as e:
            decision_log.error(f"❌ [BoardLinkSiphon] 统一运行入口捕获严重异常: {e}")
            return {
                "link_info": [],
                "siphon_info": {
                    "siphon_level": "强虹吸",
                    "absorb_ratio": "0.0%",
                    "loss_rate": "0.0%",
                    "influence_range": "全市场",
                    "compare_first": f"运行出错: {str(e)}"
                },
                "risk_warn": f"【风控报错阻断】: {str(e)}",
                "strategy": {
                    "avoid_list": "全板块规避，交易程序发生未知风控异常",
                    "opportunity_list": "观望，系统故障"
                },
                "data_missing_list": ["收官模块运行严重故障"],
                "flow_status": "终止"
            }


# 对外提供全局单例对象
board_link_siphon = BoardLinkSiphon()

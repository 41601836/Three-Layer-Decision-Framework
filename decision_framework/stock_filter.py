# -*- coding: utf-8 -*-
"""
stock_filter.py —— 第三层个股执行决策框架：子模块1 备选个股初筛
============================================================

本模块基于均线、换手率、阶段涨幅和量能四大核心技术指标，对第二层最终输出的目标板块
内的个股进行批量初筛。主要职责包括：
1. 均线趋势校验：股价收盘价站上指定周期均线；
2. 换手率合理区间校验：最新换手率处于合理范围，过滤僵尸股与过度投机标的；
3. 阶段涨幅上限校验：防止追高短期涨幅透支个股；
4. 量能温和放大校验：股价放量配合，具有主动性买盘；
5. 全流程优雅降级与缺失数据标记，确保程序健壮性。
"""

from typing import Dict, List, Any, Optional, Tuple
import pandas as pd

from decision_framework.board_link_siphon import board_link_siphon
from decision_framework.macro_query import macro_query
from config_loader import *
from decision_framework.decision_log import decision_log
from db.dao import dao


class StockFilter:
    """
    个股技术指标初筛决策单例类。
    """

    def get_latest_trade_date(self) -> Optional[str]:
        """
        获取数据库中最新的个股交易日期。
        """
        try:
            conn = dao.get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(trade_date) FROM daily_prices")
            row = cursor.fetchone()
            if row and row[0]:
                return str(row[0])
            return None
        except Exception as e:
            decision_log.error(f"❌ [StockFilter] 查询最新交易日异常: {e}")
            return None
        finally:
            conn.close()

    def get_board_stocks(self, board_name: str) -> List[Dict[str, Any]]:
        """
        1. 获取板块内全部个股基础行情数据。
        
        参数:
            board_name: 板块名称
            
        返回:
            个股基础行情数据列表，每个元素包含代码、名称及最近 MA_PERIOD 天的价格与指标序列。
        """
        decision_log.info(f"📊 [StockFilter] 开始获取板块 [{board_name}] 成分股历史行情数据...")
        latest_date = self.get_latest_trade_date()
        if not latest_date:
            decision_log.warning(f"⚠️ [StockFilter] 无法确定最新交易日，板块 [{board_name}] 行情获取失败。")
            return []

        conn = dao.get_conn()
        cursor = conn.cursor()
        try:
            # A. 查询该板块的成分股列表
            cursor.execute(
                "SELECT ts_code, name FROM stock_list WHERE industry = ?", (board_name,)
            )
            stocks = [{"ts_code": r[0], "name": r[1]} for r in cursor.fetchall()]
            if not stocks:
                decision_log.warning(f"⚠️ [StockFilter] 板块 [{board_name}] 未检索到任何成分股。")
                return []

            stock_data_list = []
            # B. 批量获取每个个股的日线和指标序列
            for stk in stocks:
                ts_code = stk["ts_code"]
                name = stk["name"]
                
                # 查询最新一天及往前的最近 MA_PERIOD 天历史记录
                sql = """
                    SELECT dp.trade_date, dp.close, dp.vol, db.turnover_rate, db.volume_ratio
                    FROM daily_prices dp
                    LEFT JOIN daily_basic db ON dp.ts_code = db.ts_code AND dp.trade_date = db.trade_date
                    WHERE dp.ts_code = ? AND dp.trade_date <= ?
                    ORDER BY dp.trade_date DESC
                    LIMIT ?
                """
                cursor.execute(sql, (ts_code, latest_date, MA_PERIOD))
                rows = cursor.fetchall()
                
                # 数据行应按照时间从老到新排列（即升序，方便计算累计涨幅和均值）
                rows.reverse()
                
                history = []
                for r in rows:
                    history.append({
                        "trade_date": r[0],
                        "close": float(r[1]) if r[1] is not None else None,
                        "vol": float(r[2]) if r[2] is not None else None,
                        "turnover_rate": float(r[3]) if r[3] is not None else None,
                        "volume_ratio": float(r[4]) if r[4] is not None else None
                    })
                
                stock_data_list.append({
                    "stock_code": ts_code,
                    "stock_name": name,
                    "board_name": board_name,
                    "history": history
                })
                
            return stock_data_list
        except Exception as e:
            decision_log.error(f"❌ [StockFilter] 查询板块 [{board_name}] 行情出现数据库异常: {e}")
            return []
        finally:
            conn.close()

    def check_single_stock(self, stock_data: Dict[str, Any]) -> Tuple[bool, List[str], str, bool]:
        """
        2. 单只个股多条件校验（均线、换手、涨幅、量能）。
        
        校验逻辑:
            1. 股价站上 MA_PERIOD 日均线；
            2. 换手率介于 TURNOVER_MIN ~ TURNOVER_MAX 之间；
            3. 阶段累计涨幅 <= STAGE_GAIN_LIMIT；
            4. 近期成交量 >= 基准成交量 * VOLUME_RATIO。
            
        返回:
            is_pass: 是否全部通过
            check_detail: 校验明细信息列表
            exclude_reason: 被排除的原因
            is_missing: 是否因为关键数据缺失而被排除
        """
        history = stock_data.get("history", [])
        code = stock_data.get("stock_code", "")
        name = stock_data.get("stock_name", "")

        # A. 校验历史天数是否足够
        if len(history) < MA_PERIOD:
            reason = f"历史价格数据天数不足 {MA_PERIOD} 天(实际 {len(history)} 天)"
            decision_log.warning(f"⚠️ [StockFilter] 个股 [{code} ({name})] 交易日不足被排除: {reason}")
            return False, [], reason, True

        # B. 校验最新指标及历史项的非空完整性
        none_fields = []
        for i, h in enumerate(history):
            if h["close"] is None:
                none_fields.append("收盘价")
            if h["vol"] is None:
                none_fields.append("成交量")
            # 换手率只需要校验最新一天的，如果历史中夹杂少量空值一般放行，但最新一天必须完整
            if i == len(history) - 1 and h["turnover_rate"] is None:
                none_fields.append("最新换手率")
                
        if none_fields:
            reason = f"关键指标缺失: {', '.join(list(set(none_fields)))}"
            decision_log.warning(f"⚠️ [StockFilter] 个股 [{code} ({name})] 指标缺失被排除: {reason}")
            return False, [], reason, True

        # C. 提取校验指标
        close_list = [h["close"] for h in history]
        vol_list = [h["vol"] for h in history]
        
        close_latest = history[-1]["close"]
        vol_latest = history[-1]["vol"]
        turnover_latest = history[-1]["turnover_rate"]

        # 1. 均线条件校验
        ma_val = sum(close_list) / len(close_list)
        ma_pass = (close_latest >= ma_val)

        # 2. 换手率条件校验 (数据库 turnover_rate 是百分比分子形式，需除以 100 转换)
        turnover_val = turnover_latest / 100.0
        turnover_pass = (TURNOVER_MIN <= turnover_val <= TURNOVER_MAX)

        # 3. 阶段累计涨幅校验
        close_earliest = history[0]["close"]
        if close_earliest <= 0:
            return False, [], "最早交易日价格为0或负数", True
        stage_gain = (close_latest - close_earliest) / close_earliest
        gain_pass = (stage_gain <= STAGE_GAIN_LIMIT)

        # 4. 量能配合条件校验 (最新成交量相比于 MA_PERIOD 均量温和放大)
        avg_vol = sum(vol_list) / len(vol_list)
        if avg_vol <= 0:
            return False, [], "历史平均成交量为0或负数", True
        vol_ratio = vol_latest / avg_vol
        volume_pass = (vol_ratio >= VOLUME_RATIO)

        # D. 整理明细及排除原因
        check_detail = [
            f"均线条件: {'满足' if ma_pass else '未满足'} (最新收盘价 {close_latest:.2f} VS {MA_PERIOD}日均线 {ma_val:.2f})",
            f"换手率条件: {'满足' if turnover_pass else '未满足'} (换手率 {turnover_latest:.2f}%，要求 {TURNOVER_MIN*100:.1f}% ~ {TURNOVER_MAX*100:.1f}%)",
            f"阶段涨幅条件: {'满足' if gain_pass else '未满足'} (阶段累计涨幅 {stage_gain:.2%}，上限 {STAGE_GAIN_LIMIT:.2%})",
            f"量能条件: {'满足' if volume_pass else '未满足'} (最新成交量是均量的 {vol_ratio:.2f}倍，要求 {VOLUME_RATIO:.1f}倍)"
        ]

        is_pass = ma_pass and turnover_pass and gain_pass and volume_pass
        
        exclude_reasons = []
        if not ma_pass:
            exclude_reasons.append("股价未站上指定周期均线")
        if not turnover_pass:
            exclude_reasons.append("换手率偏离合理区间")
        if not gain_pass:
            exclude_reasons.append("近期累计涨幅超上限")
        if not volume_pass:
            exclude_reasons.append("成交量放大不足")
            
        exclude_reason = "; ".join(exclude_reasons) if not is_pass else ""

        if is_pass:
            decision_log.info(f"✅ [StockFilter] 个股 [{code} ({name})] 满足所有初筛条件入池")
        else:
            decision_log.info(f"❌ [StockFilter] 个股 [{code} ({name})] 未通过初筛，原因: {exclude_reason}")

        return is_pass, check_detail, exclude_reason, False

    def batch_filter(self, board_list: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
        """
        3. 批量遍历板块及个股，执行全量初筛。
        
        参数:
            board_list: 待筛选板块名称列表
            
        返回:
            candidate_stocks: 备选个股列表
            exclude_stocks: 被排除个股列表
            data_missing_list: 数据缺失清单
        """
        candidate_stocks = []
        exclude_stocks = []
        data_missing_list = []

        for board_name in board_list:
            stock_data_list = self.get_board_stocks(board_name)
            for stock_data in stock_data_list:
                code = stock_data["stock_code"]
                name = stock_data["stock_name"]
                
                try:
                    is_pass, check_detail, exclude_reason, is_missing = self.check_single_stock(stock_data)
                    
                    if is_missing:
                        data_missing_list.append(f"{code}({name}): {exclude_reason}")
                        exclude_stocks.append({
                            "stock_code": code,
                            "stock_name": name,
                            "exclude_reason": f"数据缺失: {exclude_reason}"
                        })
                    elif is_pass:
                        candidate_stocks.append({
                            "stock_code": code,
                            "stock_name": name,
                            "check_detail": check_detail
                        })
                    else:
                        exclude_stocks.append({
                            "stock_code": code,
                            "stock_name": name,
                            "exclude_reason": exclude_reason
                        })
                except Exception as e:
                    decision_log.error(f"❌ [StockFilter] 过滤个股 [{code} ({name})] 出现未知异常: {e}")
                    data_missing_list.append(f"{code}({name}): 筛选过程捕获内部异常")
                    exclude_stocks.append({
                        "stock_code": code,
                        "stock_name": name,
                        "exclude_reason": f"过滤异常: {str(e)}"
                    })

        return candidate_stocks, exclude_stocks, data_missing_list

    def run(self, board_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        4. 统一对外入口。
        
        参数:
            board_result: 第二层 board_link_siphon.py 输出的字典结果
            
        返回:
            标准返回字典格式结果。
        """
        decision_log.info("🚀 [StockFilter] 开始运行第三层个股初筛引擎...")
        
        # A. 检查第二层收官是否拦截终止
        if board_result is not None and board_result.get("flow_status") == "终止":
            decision_log.warning("⚠️ [StockFilter] 检测到第二层决策被风控 [终止]，直接终止本层筛选流程。")
            return {
                "filter_board": "无",
                "candidate_stocks": [],
                "exclude_stocks": [],
                "data_missing_list": [],
                "flow_status": "终止"
            }

        # B. 获取需要执行初筛的目标板块列表
        try:
            from decision_framework.board_rank import board_rank
            rank_res = board_rank.run()
            if rank_res.get("flow_status") == "终止":
                decision_log.warning("⚠️ [StockFilter] 前置板块评级状态为 [终止]，终止个股初筛。")
                return {
                    "filter_board": "无",
                    "candidate_stocks": [],
                    "exclude_stocks": [],
                    "data_missing_list": [],
                    "flow_status": "终止"
                }
            
            board_list = [b["board_name"] for b in rank_res.get("board_list", [])]
        except Exception as e:
            decision_log.error(f"❌ [StockFilter] 串接前置板块选择流程时发生异常: {e}")
            return {
                "filter_board": "未知",
                "candidate_stocks": [],
                "exclude_stocks": [],
                "data_missing_list": [f"前置板块数据获取异常: {str(e)}"],
                "flow_status": "终止"
            }

        if not board_list:
            decision_log.warning("⚠️ [StockFilter] 未能检索到可操作目标板块，本次流程结束。")
            return {
                "filter_board": "无",
                "candidate_stocks": [],
                "exclude_stocks": [],
                "data_missing_list": [],
                "flow_status": "继续"
            }

        # C. 批量个股初筛
        filter_board_str = ", ".join(board_list)
        candidate_stocks, exclude_stocks, data_missing_list = self.batch_filter(board_list)
        
        result = {
            "filter_board": filter_board_str,
            "candidate_stocks": candidate_stocks,
            "exclude_stocks": exclude_stocks,
            "data_missing_list": data_missing_list,
            "flow_status": "继续"
        }
        
        decision_log.info(
            f"✅ [StockFilter] 个股初筛运行完毕。板块: [{filter_board_str}] | "
            f"候选备选数: {len(candidate_stocks)} | 被排除数: {len(exclude_stocks)} | 缺失数: {len(data_missing_list)}"
        )
        return result


# 对外提供全局单例对象
stock_filter = StockFilter()

# -*- coding: utf-8 -*-
from typing import List, Dict
from app.dao.base_dao import execute_query
from app.services.layer1_engine import run_full_diagnosis

class SnifferEngine:
    def __init__(self):
        pass

    def run_sniffing(self, trade_date: str) -> Dict:
        """
        运行横盘吸筹主力嗅探逻辑 (v3 Final)
        """
        # 获取最新的宏观打分，用于风控仓位管理
        macro_result = run_full_diagnosis()
        macro_score = macro_result.get("score", 0)
        
        # 定义风控仓位上限
        if macro_score >= 4:
            position_limit = "20%"
        else:
            position_limit = "10%"

        # 连表查询所有活跃股票的近三日数据进行综合判定
        # 需要的数据：
        # 1. 价格和振幅: daily_prices (连续三日数据，判断是否下跌/横盘，及单日振幅)
        # 2. 资金净流入: moneyflow (大单+特大单净流入)
        # 3. 股东户数: stk_holdernumber (连续下降)
        
        # 为了高效，我们先找到基准日期
        res = execute_query("SELECT MAX(trade_date) as max_date FROM daily_prices")
        max_date = res[0]['max_date'] if res and res[0]['max_date'] else trade_date
        
        # 取最近 3 个交易日
        dates_res = execute_query(
            "SELECT DISTINCT trade_date FROM daily_prices WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 3", 
            (max_date,)
        )
        recent_dates = [r['trade_date'] for r in dates_res]
        if not recent_dates:
            return {"status": "error", "msg": "数据不足", "candidates": []}
            
        latest_date = recent_dates[0]
        
        # 因为在 SQLite 中进行复杂的时间序列横向对比比较困难，我们用 Python 处理
        # 1. 取最新一日的基本面量价和资金
        sql_latest = """
            SELECT 
                d.ts_code, b.name, b.industry,
                d.close, d.high, d.low, d.pct_chg, d.amount,
                m.buy_lg_amount, m.sell_lg_amount, m.buy_elg_amount, m.sell_elg_amount
            FROM daily_prices d
            JOIN stock_list b ON d.ts_code = b.ts_code
            LEFT JOIN moneyflow m ON d.ts_code = m.ts_code AND d.trade_date = m.trade_date
            WHERE d.trade_date = ? AND d.amount > 10000 -- 过滤无交易的
        """
        latest_data = execute_query(sql_latest, (latest_date,))
        
        # 2. 取近三日资金流入和价格变化（用于三日背离）
        sql_history = f"""
            SELECT d.ts_code, d.trade_date, d.pct_chg, 
                   (COALESCE(m.buy_lg_amount, 0) + COALESCE(m.buy_elg_amount, 0) - COALESCE(m.sell_lg_amount, 0) - COALESCE(m.sell_elg_amount, 0)) as net_inflow
            FROM daily_prices d
            LEFT JOIN moneyflow m ON d.ts_code = m.ts_code AND d.trade_date = m.trade_date
            WHERE d.trade_date IN ({','.join(['?']*len(recent_dates))})
        """
        history_rows = execute_query(sql_history, tuple(recent_dates))
        
        history_map = {}
        for r in history_rows:
            code = r['ts_code']
            if code not in history_map:
                history_map[code] = []
            history_map[code].append(r)
            
        # 3. 取股东户数
        sql_holder = """
            SELECT ts_code, end_date, holder_num 
            FROM stk_holdernumber 
            ORDER BY ts_code, end_date DESC
        """
        holder_rows = execute_query(sql_holder)
        holder_map = {}
        for r in holder_rows:
            code = r['ts_code']
            if code not in holder_map:
                holder_map[code] = []
            if len(holder_map[code]) < 4:
                holder_map[code].append(r)
                
        # 模拟大盘当日表现
        index_res = execute_query("SELECT pct_chg FROM daily_prices WHERE ts_code='000001.SH' AND trade_date=?", (latest_date,))
        index_pct_chg = index_res[0]['pct_chg'] if index_res else -1.5 # 默认跌破1%用于测试

        candidates = []
        for row in latest_data:
            ts_code = row['ts_code']
            score = 0
            signals_triggered = []
            
            # --- 核心因子 1：主力资金净流入 ---
            buy_lg = row['buy_lg_amount'] or 0
            sell_lg = row['sell_lg_amount'] or 0
            buy_elg = row['buy_elg_amount'] or 0
            sell_elg = row['sell_elg_amount'] or 0
            net_main_flow = (buy_lg + buy_elg) - (sell_lg + sell_elg)
            
            core_money = False
            if net_main_flow > 0:
                core_money = True
                score += 40
                signals_triggered.append("主力净流入")

            # --- 核心因子 2：股东户数连续下降 ---
            core_holder = False
            holders = holder_map.get(ts_code, [])
            if len(holders) >= 2:
                # 只要最新一期比上一期少即算下降
                h0 = holders[0]['holder_num']
                h1 = holders[1]['holder_num']
                if h0 is not None and h1 is not None and h0 < h1:
                    core_holder = True
                    score += 40
                    signals_triggered.append("股东户数下降")

            # 如果没有核心因子，直接过滤
            if not core_money and not core_holder:
                continue

            # --- 增强信号 1：三日背离 ---
            enhance_divergence = False
            hist = history_map.get(ts_code, [])
            if len(hist) >= 3:
                # 连续3日主力流入，但3日总涨幅 <= 0
                inflow_3d = all(h['net_inflow'] > 0 for h in hist)
                pct_sum = sum(h['pct_chg'] for h in hist if h['pct_chg'] is not None)
                if inflow_3d and pct_sum <= 0:
                    enhance_divergence = True
                    score += 10
                    signals_triggered.append("三日背离")

            # --- 增强信号 2：振幅异常 ---
            enhance_amplitude = False
            high = row['high'] or 0
            low = row['low'] or 0
            close = row['close'] or 1
            amplitude = (high - low) / close * 100
            
            if amplitude < 3.0 and index_pct_chg <= -1.0:
                enhance_amplitude = True
                score += 10
                signals_triggered.append("抗跌振幅异常")

            # 评分与过滤门槛
            if score >= 80:
                signal_type = "强信号"
            elif score >= 60:
                signal_type = "中信号"
            else:
                continue # < 60分过滤

            # 计算止损位：MAX(买入价 × 0.95, 日低点 × 0.98)
            stop_loss_1 = close * 0.95
            stop_loss_2 = low * 0.98
            stop_loss = max(stop_loss_1, stop_loss_2)

            candidates.append({
                "ts_code": ts_code,
                "name": row['name'],
                "industry": row['industry'],
                "score": score,
                "signal_type": signal_type,
                "signals": signals_triggered,
                "close": close,
                "stop_loss": round(stop_loss, 2),
                "position_limit": position_limit
            })
            
        # 按分数排序
        candidates.sort(key=lambda x: x['score'], reverse=True)
        
        return {
            "status": "success",
            "data_date": latest_date,
            "macro_score": macro_score,
            "position_limit": position_limit,
            "total_count": len(candidates),
            "candidates": candidates
        }

# 实例化单例
sniffer_engine = SnifferEngine()

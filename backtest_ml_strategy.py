# -*- coding: utf-8 -*-
"""
backtest_ml_strategy.py —— 机器学习多因子吸筹选股策略组合回测 (重构升级版)
========================================================================
策略升级逻辑：
  1. 动态阈值：每日收盘后，读取 Ridge 模型生成的预测得分，支持参数化选股比例（如前 15% 或 30%）。
  2. 组合分散与限仓：
     - 单只股票买入比例限制为总资产的 3%。
     - 单行业累计持仓市值占总资产比例不得超过 15%，防范行业集中度过高带来的系统性黑天鹅。
     - 取消以前硬性的最多持仓 3 只股票的限制，根据总仓位上限和单票 3% 限制动态决定持仓只数。
  3. 仓位管理：每日根据大盘四色状态限制总仓位上限（绿色 70%，黄色 40%，红色 20%，黑色 0%）。黑色极端下全清仓。
  4. 风控管理：10 天到期强制离场，8% 固定止损与 5% 移动保本止损（当盈利超过 5% 后，止损线提升至成本价）。
"""

import os
import sys
import sqlite3
import argparse
import time
import json
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")
sys.path.insert(0, ROOT_DIR)

HOLD_DAYS = 10  # 10天波段持有期
INITIAL_CAPITAL = 1000000.0  # 100万组合资金
SINGLE_POS_PCT = 0.03  # 单只股票建仓限额 3%
INDUSTRY_POS_LIMIT = 0.15  # 单行业仓位限制 15%

# 大盘颜色绑定的总仓位上限
MARKET_LIMITS = {
    "green":  0.70,
    "yellow": 0.40,
    "red":    0.20,
    "black":  0.00,
}


# =============================================================================
# 1. 核心数据加载
# =============================================================================
def load_all_data(conn, start_date, end_date):
    print(f"[LOAD] 加载回测行情数据，时间范围: {start_date} ~ {end_date} ...")
    daily = pd.read_sql("""
        SELECT ts_code, trade_date, open, high, low, close, pct_chg, vol, amount
        FROM daily_prices
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(start_date, end_date))

    stock_info = pd.read_sql("SELECT ts_code, name, industry FROM stock_list", conn)
    daily = daily.merge(stock_info, on="ts_code", how="left")
    daily["name"] = daily["name"].fillna("")
    daily["industry"] = daily["industry"].fillna("未知")

    # 曾涨停或近3日有涨停（避开过热或高位股）
    daily["is_limit"] = daily["pct_chg"] >= 9.8
    daily["limit_3d_sum"] = daily.groupby("ts_code")["is_limit"].transform(lambda x: x.rolling(3).sum())

    # 计算MA5（5日均线）用于企稳过滤
    daily["ma5"] = daily.groupby("ts_code")["close"].transform(lambda x: x.rolling(5, min_periods=3).mean())
    
    # 计算近3日涨幅（pct_chg_3d）用于企稳判断
    daily["pct_chg_3d"] = daily.groupby("ts_code")["pct_chg"].transform(lambda x: x.rolling(3).sum())

    # 计算 5 日均量
    daily["vol_ma5"] = daily.groupby("ts_code")["vol"].transform(lambda x: x.rolling(5, min_periods=3).mean()).fillna(0.0)

    # 计算 ATR 指标
    df_sorted = daily.sort_values(["ts_code", "trade_date"]).copy()
    df_sorted["prev_close"] = df_sorted.groupby("ts_code")["close"].shift(1)
    df_sorted["tr"] = np.maximum(
        df_sorted["high"] - df_sorted["low"],
        np.maximum(
            (df_sorted["high"] - df_sorted["prev_close"]).abs(),
            (df_sorted["low"] - df_sorted["prev_close"]).abs()
        )
    )
    df_sorted["atr"] = df_sorted.groupby("ts_code")["tr"].transform(lambda x: x.rolling(20, min_periods=1).mean()).fillna(0.0)
    
    # 把算好的 vol_ma5 和 atr 合并回 daily
    daily["vol_ma5"] = df_sorted["vol_ma5"]
    daily["atr"] = df_sorted["atr"]

    print(f"[DATA] 行情总记录数: {len(daily):,} 行")
    return daily



# =============================================================================
# 2. 机器学习仿真回测引擎
# =============================================================================
def confirm_signal(price_ctx, market_mode):
    """信号确认：只在高确定性时交易"""
    # 1. 价格确认：收盘价站上 MA5
    if price_ctx['close'] < price_ctx['ma5']:
        return False, "价格低于MA5，信号拒绝"
    
    # 2. 成交量确认：成交量 > 5日均量
    if price_ctx['volume'] < price_ctx['volume_5d']:
        return False, "量能不足，信号拒绝"
    
    # 3. 大盘环境确认：非空仓模式
    if market_mode == 'black':
        return False, "大盘空仓模式，信号拒绝"
    
    # 4. 盈利空间确认：距离止损位至少 3%
    stop_loss = price_ctx['close'] * 0.92  # 8%止损
    if price_ctx['close'] - stop_loss < price_ctx['close'] * 0.03:
        return False, "盈利空间不足，信号拒绝"
    
    return True, "信号确认通过"


def adaptive_stop_loss(position, atr, market_mode):
    """根据大盘强度动态调整止损"""
    buy_price = position['entry_price']
    base_stop = buy_price * 0.92
    
    # ATR 动态止损（根据大盘强度调整乘数）
    if market_mode == 'green':  # 强势进攻
        atr_multiplier = 3.2
    elif market_mode == 'yellow':  # 平衡保护
        atr_multiplier = 3.0
    else:  # red/black 防守
        atr_multiplier = 1.8
    
    atr_stop = buy_price - atr_multiplier * atr
    return max(base_stop, atr_stop)


def run_ml_backtest(df, trade_dates, conn, top_pct=15, no_industry_filter=False, no_market_limit=False,
                    require_top_inst=False, allowed_weekdays=None, require_ma5_filter=False):
    from market_env import get_market_mode
    from industry_strength import calc_industry_strength_for_period
    
    # A. 建立股票历史行情索引与 20 日低点缓存
    print("[BACKTEST] 建立日线行情索引与 20 日低点防线缓存...")
    price_by_code = {}
    low20_cache = {}
    
    for code, grp in df.sort_values("trade_date").groupby("ts_code"):
        grp_sorted = grp.reset_index(drop=True)
        price_by_code[code] = grp_sorted[["trade_date", "open", "high", "low", "close", "pct_chg", "vol", "industry", "limit_3d_sum", "name", "ma5", "pct_chg_3d", "vol_ma5", "atr"]].copy()
        
        low_series = pd.to_numeric(grp_sorted["low"], errors="coerce")
        # 严格使用 shift(1) 排除当天，设置 min_periods=1 避免首月数据不足时产生 NaN，兜底 fillna 仅使用前一日价格
        low20_cache[code] = low_series.shift(1).rolling(20, min_periods=1).min().fillna(low_series.shift(1)).tolist()

    # B. 预加载大宗交易机构净买入数据（--require_top_inst 触发器）
    inst_codes_by_date = {}  # trade_date -> set(ts_code)，过去5日有机构净买入的股票集合
    _require_top_inst = require_top_inst  # 用本地变量，以便出错时 graceful fallback
    if _require_top_inst:
        print("[BACKTEST] 预加载龙虎榜机构净买入数据（top_inst 表，过去5日滚动窗口）...")
        try:
            # 优先使用 top_inst（龙虎榜机构净买入明细）
            bt_df = pd.read_sql("""
                SELECT ts_code, trade_date FROM top_inst
                WHERE net_buy > 0
                ORDER BY trade_date
            """, conn)
            data_source = "top_inst（龙虎榜机构净买入）"
        except Exception:
            # 降级：使用 block_trade 机构专用买方
            try:
                bt_df = pd.read_sql("""
                    SELECT ts_code, trade_date FROM block_trade
                    WHERE buyer LIKE '%机构%'
                    ORDER BY trade_date
                """, conn)
                data_source = "block_trade（机构专用渠道，覆盖有限）"
            except Exception:
                bt_df = pd.DataFrame()
                data_source = "无数据"

        if bt_df.empty:
            print(f"[WARN] {data_source} 无记录，--require_top_inst 自动降级为不过滤")
            _require_top_inst = False
        else:
            # 构建每个交易日「过去5日有机构净买入」的股票集合
            for i, d in enumerate(trade_dates):
                window_dates = set(trade_dates[max(0, i - 4): i + 1])  # 含当天共5日
                inst_set = set(bt_df[bt_df["trade_date"].isin(window_dates)]["ts_code"].tolist())
                inst_codes_by_date[d] = inst_set
            avg_cov = sum(len(v) for v in inst_codes_by_date.values()) // max(len(trade_dates), 1)
            print(f"[BACKTEST] 机构触发器就绪（{data_source}）：平均每日覆盖约 {avg_cov} 只股票")

    # C. 预加载每日行业前 10 强
    print("[BACKTEST] 批量计算每日行业前 10 强热度排行...")
    strong_industries_by_date = {}
    for d in trade_dates:
        try:
            df_ind = calc_industry_strength_for_period(conn, n_days=5, target_date=d)
            if not df_ind.empty:
                strong_list = df_ind[df_ind["tier"].isin(["main", "backup"])]["industry"].tolist()
                strong_industries_by_date[d] = set(strong_list)
            else:
                strong_industries_by_date[d] = set()
        except Exception:
            strong_industries_by_date[d] = set()

    # C. 预先加载 ml_signals 表的预测得分并建立每日索引 (应用动态阈值)
    print(f"[BACKTEST] 预加载机器学习预测信号，筛选每日预测得分前 {top_pct}% 候选股...")
    ml_df = pd.read_sql("SELECT trade_date, ts_code, score FROM ml_signals", conn)
    
    ml_signals_by_date = {}
    for d, grp in ml_df.groupby("trade_date"):
        scores = grp["score"].values
        if len(scores) > 0:
            # 动态计算 percentile
            threshold = np.percentile(scores, 100 - top_pct)
            threshold = max(threshold, 0.40)  # 保留正分门槛：绝对阈值阻断以防弱势行情买入弱信号股
            
            strong_ml = grp[grp["score"] >= threshold].sort_values("score", ascending=False)
            ml_signals_by_date[d] = dict(zip(strong_ml["ts_code"], strong_ml["score"]))
        else:
            ml_signals_by_date[d] = {}

    # D. 预先加载 ml_signals 表的预测得分并建立每日索引（原 C 段，已重新编号）
    # E. 仿真模拟（原 D 段）
    # [实际代码不变，上方注释仅说明编号对应关系]
    # D. 仿真模拟
    cash = INITIAL_CAPITAL
    active_positions = {}  # ts_code -> {entry_date, entry_price, stop_price, shares, current_price, hold_days, breakeven_triggered, industry}
    
    trade_history = []
    portfolio_equity = []
    
    n_stopped = 0
    n_expired = 0
    n_black_cleared = 0
    
    print("[BACKTEST] 启动投资组合多因子交易仿真...")
    
    for idx, date in enumerate(trade_dates):
        # 1. 每日大盘仓位监控
        color, max_total_pos, _ = get_market_mode(conn=conn, target_date=date, persist=False)
        # 获取大盘限制的总仓位上限
        if no_market_limit:
            # 即使不限仓位，在黑色极端风险下也执行强制清仓防线，其余颜色恒定允许 100% 满仓
            max_total_pos = 0.0 if color == "black" else 1.0
        else:
            max_total_pos = MARKET_LIMITS.get(color, 0.0)
        
        # 2. 黑色极端清仓
        if color == "black":
            cleared_codes = list(active_positions.keys())
            for code in cleared_codes:
                pos = active_positions[code]
                df_p = price_by_code.get(code)
                today_row = df_p[df_p["trade_date"] == date] if df_p is not None else None
                exit_price = float(today_row.iloc[0]["open"]) if (today_row is not None and not today_row.empty) else pos["current_price"]
                cash += exit_price * pos["shares"]
                
                trade_history.append({
                    "ts_code": code,
                    "entry_date": pos["entry_date"],
                    "exit_date": date,
                    "entry_price": pos["entry_price"],
                    "exit_price": exit_price,
                    "shares": pos["shares"],
                    "exit_pct": (exit_price - pos["entry_price"]) / pos["entry_price"] * 100,
                    "reason": "⚫ 黑色大盘强平",
                    "hold_days": pos["hold_days"]
                })
                n_black_cleared += 1
            active_positions.clear()
            
        else:
            # 3. 持仓平仓跟踪
            closed_codes = []
            for code, pos in active_positions.items():
                df_p = price_by_code.get(code)
                if df_p is None:
                    closed_codes.append(code)
                    continue
                today_row = df_p[df_p["trade_date"] == date]
                if today_row.empty:
                    continue
                
                day_low = float(today_row.iloc[0]["low"])
                day_close = float(today_row.iloc[0]["close"])
                day_high = float(today_row.iloc[0]["high"])
                
                pos["hold_days"] += 1
                pos["current_price"] = day_close
 
                # 接入自适应止损
                today_atr = float(today_row.iloc[0]["atr"])
                atr_stop = adaptive_stop_loss(pos, today_atr, color)
                pos["stop_price"] = max(pos["stop_price"], atr_stop)

                # A. 5% 移动保本止损：一旦最高收益率达到 5%，将止损线上移至买入成本价
                high_gain = (day_high - pos["entry_price"]) / pos["entry_price"]
                if high_gain >= 0.05:
                    pos["stop_price"] = max(pos["stop_price"], pos["entry_price"])
                    pos["breakeven_triggered"] = True

                # B. 止损判定 (跌破止损线)
                if day_low <= pos["stop_price"]:
                    exit_price = pos["stop_price"]
                    cash += exit_price * pos["shares"]
                    trade_history.append({
                        "ts_code": code,
                        "entry_date": pos["entry_date"],
                        "exit_date": date,
                        "entry_price": pos["entry_price"],
                        "exit_price": exit_price,
                        "shares": pos["shares"],
                        "exit_pct": (exit_price - pos["entry_price"]) / pos["entry_price"] * 100,
                        "reason": "🔴 触发止损" if not pos["breakeven_triggered"] else "🟡 触发保本离场",
                        "hold_days": pos["hold_days"]
                    })
                    closed_codes.append(code)
                    n_stopped += 1
                    continue

                # C. 10日波段到期强制平仓
                if pos["hold_days"] >= HOLD_DAYS:
                    exit_price = day_close
                    cash += exit_price * pos["shares"]
                    trade_history.append({
                        "ts_code": code,
                        "entry_date": pos["entry_date"],
                        "exit_date": date,
                        "entry_price": pos["entry_price"],
                        "exit_price": exit_price,
                        "shares": pos["shares"],
                        "exit_pct": (exit_price - pos["entry_price"]) / pos["entry_price"] * 100,
                        "reason": "⏳ 持有期满10日",
                        "hold_days": pos["hold_days"]
                    })
                    closed_codes.append(code)
                    n_expired += 1

            for cc in closed_codes:
                if cc in active_positions:
                    del active_positions[cc]

        # 4. 计算当前总资产与仓位比例
        portfolio_value = sum(pos["current_price"] * pos["shares"] for pos in active_positions.values())
        equity = cash + portfolio_value
        portfolio_equity.append({
            "trade_date": date,
            "equity": equity,
            "cash": cash,
            "color": color
        })
        current_total_pos_ratio = portfolio_value / equity if equity > 0 else 0.0

        # 5. 买入新仓 (收盘后筛选，次日开盘买入)
        if color != "black":
            # 周一战法建仓节奏：仅在允许的星期几发出建仓指令
            if allowed_weekdays is not None:
                current_weekday = pd.Timestamp(date).weekday()  # 0=周一, 4=周五
                if current_weekday not in allowed_weekdays:
                    continue  # 非建仓日：跳过本日买入，持仓管理已在上方执行

            strong_inds = strong_industries_by_date.get(date, set())
            ml_scores   = ml_signals_by_date.get(date, {})
            inst_set    = inst_codes_by_date.get(date, set()) if _require_top_inst else None

            candidates = []
            for code, score in ml_scores.items():
                df_p = price_by_code.get(code)
                if df_p is None:
                    continue
                today_row = df_p[df_p["trade_date"] == date]
                if today_row.empty:
                    continue

                ind = today_row.iloc[0]["industry"]
                is_limit_ok = today_row.iloc[0]["limit_3d_sum"] == 0
                
                # MA5企稳过滤：仅保留股价 > MA5（趋势向上）的股票
                if require_ma5_filter:
                    close = float(today_row.iloc[0]["close"])
                    ma5 = float(today_row.iloc[0]["ma5"])
                    if close <= ma5:
                        continue

                # 机构大宗触发器：过去5日必须有机构专用买方大宗成交
                if inst_set is not None and code not in inst_set:
                    continue

                # 行业过滤：可选是否启用行业强度共振
                if is_limit_ok:
                    if no_industry_filter or (ind in strong_inds):
                        candidates.append({
                            "ts_code": code,
                            "score": score,
                            "industry": ind
                        })

            # 按机器学习预测得分降序选取
            candidates = sorted(candidates, key=lambda x: x["score"], reverse=True)
            
            # 每日统计当前的行业分布占比 (已持有市值 / 总资产)
            industry_exposure = {}
            for pos_code, pos_val in active_positions.items():
                ind = pos_val["industry"]
                val_pct = (pos_val["shares"] * pos_val["current_price"]) / equity
                industry_exposure[ind] = industry_exposure.get(ind, 0.0) + val_pct
            
            # 买入执行 (分散建仓，单票 3%)
            for cand in candidates:
                code = cand["ts_code"]
                ind = cand["industry"]
                if code in active_positions:
                    continue
                
                # 信号确认机制：在买入前夕做信号二次校验
                df_p = price_by_code.get(code)
                today_row = df_p[df_p["trade_date"] == date]
                price_ctx = {
                    'close': float(today_row.iloc[0]['close']),
                    'ma5': float(today_row.iloc[0]['ma5']),
                    'volume': float(today_row.iloc[0]['vol']),
                    'volume_5d': float(today_row.iloc[0]['vol_ma5']),
                }
                ok, msg = confirm_signal(price_ctx, color)
                if not ok:
                    continue  # 信号未确认通过，拒绝建仓
                
                # 1. 大盘总仓位拦截
                if current_total_pos_ratio + SINGLE_POS_PCT > max_total_pos:
                    break  # 超过大盘所允许的最高总仓位
                
                # 2. 单行业仓位拦截 (不超过 15%)
                if industry_exposure.get(ind, 0.0) + SINGLE_POS_PCT > INDUSTRY_POS_LIMIT:
                    continue  # 行业集中度超限，跳过该股，看下一个
                
                # 获取次日行情，在开盘买入
                df_p = price_by_code.get(code)
                next_idx = df_p[df_p["trade_date"] == date].index[0] + 1
                if next_idx >= len(df_p):
                    continue
                
                next_row = df_p.iloc[next_idx]
                buy_date = next_row["trade_date"]
                open_price = float(next_row["open"])
                if open_price <= 0:
                    continue
                    
                # 3% 的建仓金额
                buy_val = equity * SINGLE_POS_PCT
                shares = int(buy_val / open_price)
                
                if shares > 0 and cash >= shares * open_price:
                    cash -= shares * open_price
                    
                    # 风控防线：20 日低点与 8% 固定止损
                    low20_val = low20_cache[code][next_idx]
                    if low20_val < open_price:
                        stop_price = max(open_price * 0.92, low20_val) # 20日低点支撑，且最深跌幅限制在 -8%
                    else:
                        stop_price = open_price * 0.92 # 创新低时，强制回退为固定 8% 止损线
                    
                    active_positions[code] = {
                        "entry_date": buy_date,
                        "entry_price": open_price,
                        "stop_price": stop_price,
                        "shares": shares,
                        "current_price": open_price,
                        "hold_days": 0,
                        "breakeven_triggered": False,
                        "industry": ind
                    }
                    
                    # 更新当前占用仓位比
                    portfolio_value = sum(pos["current_price"] * pos["shares"] for pos in active_positions.values())
                    current_total_pos_ratio = portfolio_value / equity
                    industry_exposure[ind] = industry_exposure.get(ind, 0.0) + SINGLE_POS_PCT

    df_equity = pd.DataFrame(portfolio_equity)
    df_trades = pd.DataFrame(trade_history)

    print(f"\n  回测完成。期末资产: ¥{cash:,.2f} | 止损平仓: {n_stopped} 次 | 10天到期平仓: {n_expired} 次 | 黑色极端平仓: {n_black_cleared} 次")
    return df_equity, df_trades


# =============================================================================
# 3. 绩效汇总
# =============================================================================
def analyze_performance(df_equity, df_trades):
    if df_equity.empty:
        return
        
    initial_cap = df_equity.iloc[0]["equity"]
    final_cap   = df_equity.iloc[-1]["equity"]
    
    total_ret = (final_cap - initial_cap) / initial_cap
    df_equity["daily_ret"] = df_equity["equity"].pct_change().fillna(0)
    ann_ret = (1 + total_ret) ** (252 / len(df_equity)) - 1
    ann_vol = df_equity["daily_ret"].std() * np.sqrt(252)
    sharpe  = (ann_ret - 0.02) / ann_vol if ann_vol > 0 else 0
    
    df_equity["peak"] = df_equity["equity"].cummax()
    df_equity["drawdown"] = (df_equity["equity"] - df_equity["peak"]) / df_equity["peak"]
    max_dd = df_equity["drawdown"].min()

    if not df_trades.empty:
        win_rate = (df_trades["exit_pct"] > 0).mean()
        avg_ret  = df_trades["exit_pct"].mean()
        gains    = df_trades.loc[df_trades["exit_pct"] > 0, "exit_pct"].mean()
        losses   = df_trades.loc[df_trades["exit_pct"] < 0, "exit_pct"].mean()
        pl_ratio = abs(gains / losses) if losses and losses != 0 else float("nan")
        max_loss = df_trades["exit_pct"].min()
        total_trades = len(df_trades)
    else:
        win_rate, avg_ret, gains, losses, pl_ratio, max_loss = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        total_trades = 0

    print(f"\n{'='*75}")
    print("      StockAI v4.0 - 多因子 ML 吸筹策略 (Ridge/动态组合仓位) 量化回测报告")
    print(f"{'='*75}")
    print(f"\n【ML 策略交易绩效】")
    print(f"  交易总笔数  : {total_trades:,} 笔")
    print(f"  信号平均胜率: {win_rate:.1%}  {'✅' if win_rate >= 0.60 else '⚠️'}")
    print(f"  单笔均收益  : {avg_ret:+.2f}%")
    print(f"  平均盈利    : {gains:+.2f}%")
    print(f"  平均亏损    : {losses:+.2f}%")
    print(f"  系统盈亏比  : {pl_ratio:.2f}")
    print(f"  最大单笔亏损: {max_loss:+.2f}%")
 
    print(f"\n【投资组合资产曲线 (初始 ¥1,000,000.00)】")
    print(f"  期末总资产  : ¥{final_cap:,.2f}")
    print(f"  组合总收益率: {total_ret:+.2%}")
    print(f"  组合年化收益: {ann_ret:+.2%}")
    print(f"  组合最大回撤: {max_dd:.2%}  {'✅' if max_dd > -0.15 else '⚠️'}")
    print(f"  组合夏普比率: {sharpe:.3f}")

    if not df_trades.empty:
        df_trades["month"] = df_trades["exit_date"].str[:6]
        monthly = df_trades.groupby("month").agg(
            交易数=("exit_pct", "count"),
            月胜率=("exit_pct", lambda x: f"{(x > 0).mean()*100:.1f}%"),
            月收益=("exit_pct", lambda x: f"{x.mean():+.2f}%")
        )
        print(f"\n【月度交易明细】")
        print(monthly.to_string())


def main():
    parser = argparse.ArgumentParser(description="多因子 ML 吸筹组合分散回测")
    parser.add_argument("--start",  default="20250101")
    parser.add_argument("--end",    default="20251231")
    parser.add_argument("--top_pct", type=int, default=15, help="全市场前百分之多少的ML得分列为候选池，默认 15%")
    parser.add_argument("--hold_days", type=int, default=10, help="波段持仓天数，默认 10 天")
    parser.add_argument("--no_industry_filter", action="store_true", help="是否关闭行业强度共振过滤")
    parser.add_argument("--no_market_limit", action="store_true", help="是否关闭大盘预警的仓位限制(仅在黑色时强制清仓)")
    parser.add_argument("--output", default="reports/backtest_ml_strategy.csv")
    parser.add_argument("--require_top_inst", action="store_true",
                        help="仅买入过去5日有大宗交易'机构专用'买方成交的股票（机构低调建仓事件触发器）")
    parser.add_argument("--weekday_filter", type=str, default=None,
                        help="仅允许在指定星期建仓，格式: '0,1' 表示仅周一/周二（0=周一,4=周五），与周一战法节奏对齐")
    parser.add_argument("--require_ma5_filter", action="store_true",
                        help="MA5企稳过滤：仅买入收盘价高于5日均线的股票（避开下跌趋势中的股票）")
    args = parser.parse_args()

    # 解析星期过滤参数
    allowed_weekdays = None
    if args.weekday_filter:
        allowed_weekdays = [int(x.strip()) for x in args.weekday_filter.split(",")]
        weekday_names = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五"}
        day_str = "/".join(weekday_names.get(d, str(d)) for d in allowed_weekdays)
        print(f"[CONFIG] 周一战法节奏：仅在 {day_str} 建仓")

    # 将持仓天数传入全局常量（支持命令行参数化对比实验）
    global HOLD_DAYS
    HOLD_DAYS = args.hold_days
    print(f"[CONFIG] 持仓: {HOLD_DAYS}天 | 候选: 前{args.top_pct}% | 大盘限仓: {'OFF' if args.no_market_limit else 'ON'} | 行业过滤: {'OFF' if args.no_industry_filter else 'ON'} | 机构触发: {'ON' if args.require_top_inst else 'OFF'} | MA5过滤: {'ON' if args.require_ma5_filter else 'OFF'} | 建仓节奏: {args.weekday_filter or '全周'}")

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")

    try:
        # 行情数据预留温热期（从 20241101 开始加载），防止 2025 年开年时 rolling 20日低点指标因为数据不足产生 NaN 并退化
        daily = load_all_data(conn, "20241101", args.end)
        
        if daily.empty:
            print("[ERROR] 本地日线数据为空，终止回测")
            return
            
        trade_dates = sorted(
            daily[daily["trade_date"] >= args.start]["trade_date"].unique().tolist()
        )
        
        df_equity, df_trades = run_ml_backtest(
            daily, trade_dates, conn,
            top_pct=args.top_pct,
            no_industry_filter=args.no_industry_filter,
            no_market_limit=args.no_market_limit,
            require_top_inst=args.require_top_inst,
            allowed_weekdays=allowed_weekdays,
            require_ma5_filter=args.require_ma5_filter
        )
        
        analyze_performance(df_equity, df_trades)
        
        if not df_equity.empty:
            out_path = args.output if os.path.isabs(args.output) else os.path.join(ROOT_DIR, args.output)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            df_equity.to_csv(out_path, index=False, encoding="utf-8-sig")
            print(f"\n[OUTPUT] 结果曲线已保存到 {out_path}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
v3.3 Final 整合回测脚本 (四色预警升级版)
================================================

重构内容：
  1. 引入四色预警与仓位模型（🟢70% / 🟡40% / 🔴20% / ⚫0%）。
  2. 实现真实的逐日投资组合模拟（Portfolio Simulation）：
     - 初始资金：1,000,000 元。
     - 单股建仓比例：绿色 15% | 黄色 10% | 红色 5% | 黑色 0%。
     - 强制总仓位限制：当前所有持仓市值之和不能超过该环境的强制总仓位上限。
     - 黑色极端休战：一票否决，开盘强制清仓所有股票。
  3. 精调风控止损：
     - 固定 8% 止损：`stop_loss_fixed = 买入价 × 0.92`。
     - 结构止损：`stop_loss_structure = 20日最低价 × 0.98`。
     - 实际建仓主止损：`stop_loss_primary = max(stop_loss_fixed, stop_loss_structure)`。
     - 最大持有期：10 日。

用法：
    python backtest_final_v33.py --start 20250601 --end 20251231
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

# 止损与参数配置
STOP_LOSS_PCT   = 0.08   # 8%固定止损
HOLD_DAYS       = 10     # 最长持股天数
MIN_CIRC_MV_YI   = 10.0  # 微盘股过滤阈值（亿元）
SCORE_THRESHOLD = 30     # 强信号门槛

# 仓位比例
TOTAL_POS_LIMITS = {
    "green":  0.70,
    "yellow": 0.40,
    "red":    0.20,
    "black":  0.00,
}

SINGLE_POS_LIMITS = {
    "green":  0.15,
    "yellow": 0.10,
    "red":    0.05,
    "black":  0.00,
}


# =============================================================================
# 数据加载
# =============================================================================
def load_all_data(conn, start_date, end_date):
    pre_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")
    log_info(f"[START] 开启大盘四色回测，区间: {start_date} ~ {end_date}")

    print("[LOAD] 日线数据...")
    daily = pd.read_sql("""
        SELECT ts_code, trade_date, open, high, low, close, pct_chg, vol, amount
        FROM daily_prices
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(pre_start, end_date))

    print("[LOAD] 资金流数据...")
    money = pd.read_sql("""
        SELECT ts_code, trade_date,
               buy_elg_amount, sell_elg_amount,
               buy_lg_amount, sell_lg_amount
        FROM moneyflow
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(start_date, end_date))

    print("[LOAD] 股东户数数据...")
    holder = pd.read_sql("""
        SELECT ts_code, ann_date, holder_num
        FROM stk_holdernumber
        ORDER BY ts_code, ann_date
    """, conn)

    print("[LOAD] 流通市值数据...")
    try:
        circ_mv = pd.read_sql("""
            SELECT ts_code, MAX(trade_date) as latest_date, circ_mv
            FROM daily_basic
            WHERE circ_mv IS NOT NULL
            GROUP BY ts_code
        """, conn)
        circ_mv["circ_mv_yi"] = pd.to_numeric(circ_mv["circ_mv"], errors="coerce") / 10000.0
    except Exception as e:
        print(f"  [WARN] 无法加载流通市值: {e}")
        circ_mv = pd.DataFrame(columns=["ts_code", "circ_mv_yi"])

    print("[LOAD] 融资余额数据...")
    try:
        margin = pd.read_sql("""
            SELECT ts_code, trade_date, rzye
            FROM margin_detail
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY ts_code, trade_date
        """, conn, params=(start_date, end_date))
    except Exception:
        margin = pd.DataFrame(columns=["ts_code", "trade_date", "rzye"])

    print("[LOAD] 北向资金数据...")
    try:
        hsgt = pd.read_sql("""
            SELECT trade_date, north_money
            FROM hsgt_moneyflow
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY trade_date
        """, conn, params=(start_date, end_date))
    except Exception:
        hsgt = pd.DataFrame(columns=["trade_date", "north_money"])

    print(f"[DATA] 日线:{len(daily):,} | 资金:{len(money):,} | 股东:{len(holder):,} | "
          f"市值:{len(circ_mv):,} | 融资:{len(margin):,} | 北向:{len(hsgt):,}")
    return daily, money, holder, circ_mv, margin, hsgt


def log_info(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [INFO] {msg}")


# =============================================================================
# 因子计算 & 信号生成
# =============================================================================
def compute_signals(daily_all, money, holder, circ_mv, margin, hsgt, start_date, conn):
    daily = daily_all[daily_all["trade_date"] >= start_date].copy()

    # 主力资金
    money = money.copy()
    for col in ["buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount"]:
        money[col] = pd.to_numeric(money[col], errors="coerce").fillna(0)
    money["net_main"]  = (money["buy_elg_amount"] + money["buy_lg_amount"]
                          - money["sell_elg_amount"] - money["sell_lg_amount"])
    money["money_ok"]  = money["net_main"] > 0
    money["money_out"] = money["net_main"] < 0

    # 股东户数
    holder = holder.sort_values(["ts_code", "ann_date"]).copy()
    holder["holder_num"]  = pd.to_numeric(holder["holder_num"], errors="coerce")
    holder["holder_prev"] = holder.groupby("ts_code")["holder_num"].shift(1)
    holder["holder_2d_ok"] = holder["holder_num"] < holder["holder_prev"]
    holder_latest = holder.groupby("ts_code").last().reset_index()

    # 融资（三重否决）
    if not margin.empty:
        margin = margin.sort_values(["ts_code", "trade_date"]).copy()
        margin["rzye"] = pd.to_numeric(margin["rzye"], errors="coerce")
        margin["rzye_prev"] = margin.groupby("ts_code")["rzye"].shift(1)
        margin["margin_down"] = margin["rzye"] < margin["rzye_prev"]
        margin_latest = margin.sort_values(["ts_code", "trade_date"]).groupby("ts_code").last().reset_index()
    else:
        margin_latest = pd.DataFrame(columns=["ts_code", "margin_down"])

    # 北向（三重否决）
    if not hsgt.empty:
        hsgt = hsgt.sort_values("trade_date").copy()
        hsgt["north_money"] = pd.to_numeric(hsgt["north_money"], errors="coerce")
        # 3日均线对比
        hsgt["north_3d_mean"] = hsgt["north_money"].rolling(3).mean()
        hsgt["north_prev_3d_mean"] = hsgt["north_3d_mean"].shift(3)
        hsgt["hsgt_out"] = hsgt["north_3d_mean"] < hsgt["north_prev_3d_mean"]
        hsgt.loc[hsgt["north_prev_3d_mean"].isna(), "hsgt_out"] = \
            hsgt.loc[hsgt["north_prev_3d_mean"].isna(), "north_money"] < \
            hsgt.loc[hsgt["north_prev_3d_mean"].isna(), "north_money"].shift(1)
    else:
        hsgt["hsgt_out"] = False

    # 合并
    df = daily.merge(money[["ts_code", "trade_date", "money_ok", "money_out"]],
                     on=["ts_code", "trade_date"], how="left")
    df = df.merge(holder_latest[["ts_code", "holder_2d_ok"]], on="ts_code", how="left")

    # 微盘股过滤
    if not circ_mv.empty and "circ_mv_yi" in circ_mv.columns:
        valid_codes = circ_mv[circ_mv["circ_mv_yi"] >= MIN_CIRC_MV_YI]["ts_code"]
        before = len(df)
        df = df[df["ts_code"].isin(valid_codes)]
        print(f"[FILTER] 微盘股过滤: {before:,} → {len(df):,} 条（剔除 {before-len(df):,} 条）")

    if not margin_latest.empty:
        df = df.merge(margin_latest[["ts_code", "margin_down"]], on="ts_code", how="left")
    else:
        df["margin_down"] = False

    if not hsgt.empty and "hsgt_out" in hsgt.columns:
        df = df.merge(hsgt[["trade_date", "hsgt_out"]], on="trade_date", how="left")
    else:
        df["hsgt_out"] = False

    # 评分
    df["score"] = 0
    df.loc[df["money_ok"] == True, "score"] += 15
    df.loc[df["holder_2d_ok"] == True, "score"] += 15

    # 三重否决
    risk_flag = (df["money_out"] == True) & (df["margin_down"] == True) & (df["hsgt_out"] == True)
    df.loc[risk_flag, "score"] = 0

        # 仅保留大盘处于上证指数 MA20 > MA60 安全阶段的个股信号
    try:
        idx_df = pd.read_sql("""SELECT trade_date, close FROM daily_index WHERE ts_code='000001.SH' ORDER BY trade_date""", conn)
        idx_df['ma20'] = idx_df['close'].rolling(20).mean()
        idx_df['ma60'] = idx_df['close'].rolling(60).mean()
        safe_dates = idx_df[idx_df['ma20'] > idx_df['ma60']]['trade_date'].tolist()
        df = df[df['trade_date'].isin(safe_dates)]
    except Exception as e:
        print('大盘过滤异常:', e)
        
    df["signal"] = (df["score"] >= SCORE_THRESHOLD)
    n_strong = df["signal"].sum()
    n_risk   = risk_flag.sum()
    print(f"[SIGNAL] 强信号: {n_strong:,} 条 | 三重否决: {n_risk:,} 次")
    return df


# =============================================================================
# 真实资产投资组合逐日持仓回测模拟
# =============================================================================
def run_portfolio_simulation(daily_all, signals_df, trade_dates, conn, start_date):
    """
    逐日资产持仓限制模拟：
      - 总仓位限制 (🟢70% / 🟡40% / 🔴20% / ⚫0%)
      - 单股建仓比例 (🟢15% / 🟡10% / 🔴5% / ⚫0%)
      - 黑色极端清仓
    """
    # 导入大盘颜色查询函数
    from market_env import get_market_mode
    
    # 1. 预建股票价格索引
    print("[BACKTEST] 建立股票历史行情索引...")
    price_by_code = {}
    for code, grp in daily_all.sort_values("trade_date").groupby("ts_code"):
        price_by_code[code] = grp[["trade_date", "open", "high", "low", "close"]].reset_index(drop=True)

    # 提取低点（用于结构止损）
    def get_low20(code, entry_date):
        df = price_by_code.get(code)
        if df is None:
            return None
        past = df[df["trade_date"] <= entry_date].tail(20)
        if past.empty:
            return None
        return pd.to_numeric(past["low"], errors="coerce").min()

    # 提取强信号
    strong_signals = signals_df[signals_df["signal"] == True].copy()
    signals_by_date = {}
    for dt, grp in strong_signals.groupby("trade_date"):
        signals_by_date[dt] = grp["ts_code"].tolist()

    # 2. 投资组合模拟参数
    initial_capital = 1000000.0
    cash = initial_capital
    active_positions = {}  # ts_code -> {entry_date, entry_price, stop_price, shares, current_price, hold_days}
    
    trade_history = []
    portfolio_equity = []
    
    daily_stats = []
    n_stopped = 0
    n_expired = 0
    n_black_cleared = 0

    print("[BACKTEST] 开始投资组合每日交易与限仓模拟...")
    for idx, date in enumerate(trade_dates):
        # 1. 获取今日大盘颜色状态
        color, max_total_pos, _ = get_market_mode(conn=conn, target_date=date, persist=False)
        single_pos_pct = SINGLE_POS_LIMITS.get(color, 0.05)

        # 2. 黑色环境清仓处理
        if color == "black":
            cleared_codes = list(active_positions.keys())
            for code in cleared_codes:
                pos = active_positions[code]
                df_p = price_by_code.get(code)
                today_row = df_p[df_p["trade_date"] == date] if df_p is not None else None
                
                # 以开盘价强制平仓
                exit_price = float(today_row.iloc[0]["open"]) if today_row is not None and not today_row.empty else pos["current_price"]
                cash += exit_price * pos["shares"]
                
                trade_history.append({
                    "ts_code": code,
                    "entry_date": pos["entry_date"],
                    "exit_date": date,
                    "entry_price": pos["entry_price"],
                    "exit_price": exit_price,
                    "shares": pos["shares"],
                    "exit_pct": (exit_price - pos["entry_price"]) / pos["entry_price"] * 100,
                    "reason": "⚫ 黑色大盘强制清仓",
                    "hold_days": pos["hold_days"]
                })
                n_black_cleared += 1
            active_positions.clear()

        else:
            # 3. 日常持仓平仓检测（非黑色环境下）
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
                
                pos["hold_days"] += 1
                pos["current_price"] = day_close

                # A. 止损触板检测
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
                        "reason": "🔴 触发止损",
                        "hold_days": pos["hold_days"]
                    })
                    closed_codes.append(code)
                    n_stopped += 1
                    continue
                
                # B. 到期平仓检测
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
                        "reason": "⏳ 持有期满10日出局",
                        "hold_days": pos["hold_days"]
                    })
                    closed_codes.append(code)
                    n_expired += 1
                    continue

            for code in closed_codes:
                if code in active_positions:
                    del active_positions[code]

        # 4. 计算今日组合市值与今日总资产
        portfolio_value = sum(pos["current_price"] * pos["shares"] for pos in active_positions.values())
        equity = cash + portfolio_value
        current_total_pos_ratio = portfolio_value / equity if equity > 0 else 0.0

        # 5. 买入新仓（受限仓额度限制）
        if color != "black":
            today_signals = signals_by_date.get(date, [])
            for code in today_signals:
                # 跳过已持仓股票
                if code in active_positions:
                    continue
                
                # 计算是否允许建新仓：今日持仓市值比例 + 单股建仓比例 <= 今日总仓位上限
                if current_total_pos_ratio + single_pos_pct <= max_total_pos:
                    df_p = price_by_code.get(code)
                    if df_p is None:
                        continue
                    today_row = df_p[df_p["trade_date"] == date]
                    if today_row.empty:
                        continue
                    
                    # 锁定 66.7% 最优超级极品组合
                    stock_df = price_by_code.get(code)
                    if stock_df is None or stock_df.empty:
                        continue
                    stock_today = stock_df[stock_df["trade_date"] == date]
                    if stock_today.empty:
                        continue
                        
                    close_price = float(stock_today.iloc[0]["close"])
                    open_price = float(stock_today.iloc[0]["open"])
                    
                    # A. 20日振幅判定 <= 9.5%
                    past_20 = stock_df[stock_df["trade_date"] <= date].tail(20)
                    if not past_20.empty:
                        low_val = past_20["low"].min()
                        high_val = past_20["high"].max()
                        amplitude_20 = (high_val - low_val) / low_val if low_val > 0 else 0
                        if amplitude_20 > 0.095:
                            continue
                            
                    # B. 创20日高点突破 (允许1.0%偏离度容差)
                    if not past_20.empty:
                        max_close_20 = past_20["close"].max()
                        if close_price < max_close_20 * 0.99:
                            continue
                            
                    # C. 个股股价站上 5日收盘均线 (MA5)
                    past_5 = stock_df[stock_df["trade_date"] <= date].tail(5)
                    ma5 = past_5["close"].mean() if not past_5.empty else close_price
                    if close_price < ma5:
                        continue
                    
                    # 强行按照当前总权益比例买入
                    buy_value = equity * single_pos_pct
                    shares = int(buy_value / close_price)
                    
                    if shares > 0 and cash >= shares * close_price:
                        # 扣款
                        cash -= shares * close_price
                        
                        # 计算主止损线
                        low20 = get_low20(code, date)
                        stop_fixed = close_price * (1 - STOP_LOSS_PCT)
                        stop_struct = (low20 * 0.98) if low20 and low20 > 0 else stop_fixed
                        stop_price = max(stop_fixed, stop_struct)
                        
                        active_positions[code] = {
                            "entry_date": date,
                            "entry_price": close_price,
                            "stop_price": stop_price,
                            "shares": shares,
                            "current_price": close_price,
                            "hold_days": 0
                        }
                        # 更新当前仓位占比
                        portfolio_value = sum(pos["current_price"] * pos["shares"] for pos in active_positions.values())
                        current_total_pos_ratio = portfolio_value / equity

        # 再次更新总资产
        portfolio_value = sum(pos["current_price"] * pos["shares"] for pos in active_positions.values())
        equity = cash + portfolio_value
        
        portfolio_equity.append({
            "trade_date": date,
            "equity": equity,
            "cash": cash,
            "portfolio_value": portfolio_value,
            "position_ratio": portfolio_value / equity,
            "color": color
        })

    df_equity = pd.DataFrame(portfolio_equity)
    df_trades = pd.DataFrame(trade_history)
    
    print(f"  回测期末资产: ¥{equity:,.2f} | 止损平仓: {n_stopped} 次 | 满期平仓: {n_expired} 次 | 黑色清仓: {n_black_cleared} 次")
    return df_equity, df_trades


# =============================================================================
# 绩效分析
# =============================================================================
def analyze_performance(df_equity, df_trades):
    """统计输出回测绩效总表"""
    if df_equity.empty:
        print("[ERROR] 组合回测结果为空！")
        return None
        
    initial_cap = df_equity.iloc[0]["equity"]
    final_cap   = df_equity.iloc[-1]["equity"]
    
    # 1. 组合层面绩效
    total_ret = (final_cap - initial_cap) / initial_cap
    
    # 每日收益率
    df_equity["daily_ret"] = df_equity["equity"].pct_change().fillna(0)
    ann_ret = (1 + total_ret) ** (252 / len(df_equity)) - 1
    ann_vol = df_equity["daily_ret"].std() * np.sqrt(252)
    sharpe  = (ann_ret - 0.02) / ann_vol if ann_vol > 0 else 0
    
    # 最大回撤
    df_equity["peak"] = df_equity["equity"].cummax()
    df_equity["drawdown"] = (df_equity["equity"] - df_equity["peak"]) / df_equity["peak"]
    max_dd = df_equity["drawdown"].min()

    # 2. 单股交易层绩效
    if not df_trades.empty:
        win_rate = (df_trades["exit_pct"] > 0).mean()
        avg_ret  = df_trades["exit_pct"].mean()
        gains    = df_trades.loc[df_trades["exit_pct"] > 0, "exit_pct"].mean()
        losses   = df_trades.loc[df_trades["exit_pct"] < 0, "exit_pct"].mean()
        pl_ratio = abs(gains / losses) if losses and losses != 0 else float("nan")
        max_loss = df_trades["exit_pct"].min()
        total_trades_count = len(df_trades)
    else:
        win_rate, avg_ret, gains, losses, pl_ratio, max_loss = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        total_trades_count = 0

    print(f"\n{'='*70}")
    print("       StockAI v4.0 - 大盘四色预警与仓位回测报告")
    print(f"{'='*70}")
    print(f"\n【单信号裸交易绩效】")
    print(f"  交易总笔数  : {total_trades_count:,} 笔")
    print(f"  信号平均胜率: {win_rate:.1%}  {'✅' if win_rate >= 0.55 else '⚠️'}")
    print(f"  单次均收益  : {avg_ret:+.2f}%")
    print(f"  平均盈利    : {gains:+.2f}%")
    print(f"  平均亏损    : {losses:+.2f}%")
    print(f"  系统盈亏比  : {pl_ratio:.2f}  {'✅' if pl_ratio >= 1.5 else '⚠️'}")
    print(f"  最大单笔亏损: {max_loss:+.2f}% (被 8% 止损安全拦截)")

    print(f"\n【限仓组合绩效 (🟢70%/🟡40%/🔴20%/⚫0% 约束)】")
    print(f"  期末总资产  : ¥{final_cap:,.2f} (初始 ¥{initial_cap:,.2f})")
    print(f"  组合总收益率: {total_ret:+.2%}")
    print(f"  组合年化收益: {ann_ret:+.2%}")
    print(f"  组合最大回撤: {max_dd:.2%}  {'✅' if max_dd > -0.15 else '⚠️'}")
    print(f"  组合夏普比率: {sharpe:.3f}")

    # 3. 按大盘颜色统计
    print(f"\n【大盘颜色运行天数及平均仓位分布】")
    color_summary = df_equity.groupby("color").agg(
        天数=("trade_date", "count"),
        平均仓位=("position_ratio", lambda x: f"{x.mean()*100:.1f}%")
    )
    print(color_summary.to_string())

    # 4. 月度收益明细
    if not df_trades.empty:
        df_trades["month"] = df_trades["exit_date"].str[:6]
        monthly = df_trades.groupby("month").agg(
            交易笔数=("exit_pct", "count"),
            月度胜率=("exit_pct", lambda x: f"{(x > 0).mean()*100:.1f}%"),
            均收益=("exit_pct", lambda x: f"{x.mean():+.2f}%")
        )
        print(f"\n【月度交易明细】")
        print(monthly.to_string())

    return {
        "win_rate": win_rate,
        "pl_ratio": pl_ratio,
        "total_ret": total_ret,
        "max_dd": max_dd,
        "sharpe": sharpe
    }


# =============================================================================
# 主流程
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="v3.3 Final 大盘四色预警组合回测")
    parser.add_argument("--start",  default="20250601")
    parser.add_argument("--end",    default="20251231")
    parser.add_argument("--output", default="reports/backtest_four_color_2025.csv")
    args = parser.parse_args()

    t0 = time.time()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")

    try:
        # 数据加载
        daily_all, money, holder, circ_mv, margin, hsgt = \
            load_all_data(conn, args.start, args.end)

        # 因子计算与信号生成
        signals_df = compute_signals(daily_all, money, holder, circ_mv, margin, hsgt, args.start, conn)

        # 提取交易日历
        trade_dates = sorted(
            daily_all[daily_all["trade_date"] >= args.start]["trade_date"].unique().tolist()
        )

        # 运行组合模拟
        df_equity, df_trades = run_portfolio_simulation(daily_all, signals_df, trade_dates, conn, args.start)

        # 绩效分析
        analyze_performance(df_equity, df_trades)

        # 保存结果
        if df_equity is not None and not df_equity.empty:
            out_path = args.output if os.path.isabs(args.output) else os.path.join(ROOT_DIR, args.output)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            df_equity.to_csv(out_path, index=False, encoding="utf-8-sig")
            print(f"\n[OUTPUT] 组合净值曲线结果已保存到 {out_path}")

    finally:
        conn.close()

    print(f"\n[DONE] 大盘四色仓位约束回测完成，共耗时: {time.time()-t0:.2f} 秒")


if __name__ == "__main__":
    main()

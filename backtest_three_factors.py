# -*- coding: utf-8 -*-
"""
三因子共振波段交易策略回测脚本 (主力资金 + 筹码集中 + 板块强度)
============================================================
本策略基于三大核心量化因子：
  1. 板块强度因子：所属行业热度在当天名列全市场前 10 位（属于主线或备选梯队）。
  2. 筹码集中因子：最新已公告的股东户数较前一期减少（holder_num < prev_holder_num）。
  3. 主力资金因子：近5日个股主力净额累加值为正，且近5日至少有3天主力大单净流入。
交易节奏：
  - 每日盘后选股，符合三因子共振的股票在次日开盘价买入。
  - 仿真投资组合限仓机制：大盘四色仓位控制（🟢70% / 🟡40% / 🔴20% / ⚫0%），单票限额。
  - 持仓管理：最长持有 10 个交易日出局，绑定 8% 固定止损（前20日低点）与 5% 移动保本止损。
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

SINGLE_POS_LIMITS = {
    "green":  0.15,
    "yellow": 0.10,
    "red":    0.05,
    "black":  0.00,
}


# =============================================================================
# 核心数据加载
# =============================================================================
def load_all_data(conn, start_date, end_date):
    pre_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=240)).strftime("%Y%m%d")
    print(f"[LOAD] 加载行情与资金数据，预热起点: {pre_start} ~ 终点: {end_date}")

    daily = pd.read_sql("""
        SELECT ts_code, trade_date, open, high, low, close, pct_chg, vol, amount
        FROM daily_prices
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(pre_start, end_date))

    stock_info = pd.read_sql("""
        SELECT ts_code, name, industry
        FROM stock_list
    """, conn)
    daily = daily.merge(stock_info, on="ts_code", how="left")
    daily["name"] = daily["name"].fillna("")
    daily["industry"] = daily["industry"].fillna("未知")

    money = pd.read_sql("""
        SELECT ts_code, trade_date,
               buy_elg_amount, sell_elg_amount,
               buy_lg_amount, sell_lg_amount
        FROM moneyflow
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(pre_start, end_date))

    money["buy_elg_amount"] = pd.to_numeric(money["buy_elg_amount"]).fillna(0)
    money["sell_elg_amount"] = pd.to_numeric(money["sell_elg_amount"]).fillna(0)
    money["buy_lg_amount"] = pd.to_numeric(money["buy_lg_amount"]).fillna(0)
    money["sell_lg_amount"] = pd.to_numeric(money["sell_lg_amount"]).fillna(0)
    
    money["net_main"] = (money["buy_elg_amount"] + money["buy_lg_amount"] -
                         money["sell_elg_amount"] - money["sell_lg_amount"])

    print(f"[DATA] 行情记录: {len(daily):,} 行 | 资金记录: {len(money):,} 行")
    return daily, money


# =============================================================================
# 三因子仿真回测引擎
# =============================================================================
def run_three_factor_backtest(df, money, trade_dates, conn, start_date):
    from market_env import get_market_mode
    from industry_strength import calc_industry_strength_for_period
    
    # 1. 预计算主力 5 日资金因子
    print("[BACKTEST] 向量化计算个股主力资金因子...")
    mdf = money.sort_values(["ts_code", "trade_date"]).copy()
    mdf["net_main_5d_sum"] = mdf.groupby("ts_code")["net_main"].transform(lambda x: x.rolling(5).sum())
    mdf["inflow_day"] = mdf["net_main"] > 0
    mdf["inflow_5d_cnt"] = mdf.groupby("ts_code")["inflow_day"].transform(lambda x: x.rolling(5).sum())
    mdf["money_ok"] = (mdf["net_main_5d_sum"] > 0) & (mdf["inflow_5d_cnt"] >= 3)

    df = df.merge(mdf[["ts_code", "trade_date", "money_ok"]], on=["ts_code", "trade_date"], how="left")
    df["money_ok"] = df["money_ok"].fillna(False)

    # 曾涨停或近3日有涨停（过滤情绪高标，防止追高）
    df["is_limit"] = df["pct_chg"] >= 9.8
    df["limit_3d_sum"] = df.groupby("ts_code")["is_limit"].transform(lambda x: x.rolling(3).sum())

    # 2. 预建日线行情快速索引
    print("[BACKTEST] 建立股票历史行情索引与 20 日低点缓存...")
    price_by_code = {}
    low20_cache = {}
    
    for code, grp in df.sort_values("trade_date").groupby("ts_code"):
        grp_sorted = grp.reset_index(drop=True)
        price_by_code[code] = grp_sorted[["trade_date", "open", "high", "low", "close", "pct_chg", "industry", "money_ok", "limit_3d_sum", "name"]].copy()
        
        # 缓存 20 日低点
        low_series = pd.to_numeric(grp_sorted["low"], errors="coerce")
        low20_cache[code] = low_series.rolling(20).min().fillna(low_series).tolist()

    # 3. 预先批量计算每日行业强度前 10 强
    print("[BACKTEST] 批量计算 2025 全年每日行业强度排名...")
    strong_industries_by_date = {}
    t0 = time.time()
    for d in trade_dates:
        # 计算该日期下近 5 日强度排行
        try:
            df_ind = calc_industry_strength_for_period(conn, n_days=5, target_date=d)
            if not df_ind.empty:
                # 提取主力 tier = 'main' 或 'backup' 的行业名称 (前 10 名)
                strong_list = df_ind[df_ind["tier"].isin(["main", "backup"])]["industry"].tolist()
                strong_industries_by_date[d] = set(strong_list)
            else:
                strong_industries_by_date[d] = set()
        except Exception:
            strong_industries_by_date[d] = set()
    print(f"[BACKTEST] 行业强度计算完成，共 {len(strong_industries_by_date)} 天，耗时 {time.time()-t0:.1f} 秒")

    # 4. 加载股东户数全量数据（用于避免未来函数的高速比对）
    print("[BACKTEST] 预加载股东户数数据库记录...")
    holder_all = pd.read_sql("""
        SELECT ts_code, ann_date, end_date, holder_num
        FROM stk_holdernumber
        ORDER BY ts_code, end_date
    """, conn)
    holder_all["holder_num"] = pd.to_numeric(holder_all["holder_num"], errors="coerce")
    holder_all["ann_date"] = holder_all["ann_date"].fillna(holder_all["end_date"]) # 缺失公告日用截止日代替
    
    # 建立股票 -> 股东披露记录的映射列表，用于 O(1) 二分或快速匹配
    holder_by_code = {}
    for code, grp in holder_all.groupby("ts_code"):
        holder_by_code[code] = grp.sort_values("end_date")[["ann_date", "end_date", "holder_num"]].to_dict("records")

    # 5. 组合仿真
    cash = INITIAL_CAPITAL
    active_positions = {}  # ts_code -> {entry_date, entry_price, stop_price, shares, current_price, hold_days, breakeven_triggered}
    
    trade_history = []
    portfolio_equity = []
    
    n_stopped = 0
    n_expired = 0
    n_black_cleared = 0
    
    print("[BACKTEST] 开始逐日交易与限仓回测模拟...")
    
    for idx, date in enumerate(trade_dates):
        # 1. 每日获取今日大盘颜色状态以限制仓位
        color, max_total_pos, _ = get_market_mode(conn=conn, target_date=date, persist=False)
        single_pos_pct = SINGLE_POS_LIMITS.get(color, 0.05)
        
        # 2. 黑色大盘极端风险：开盘强制清仓
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
            # 3. 日常持仓平仓跟踪
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

                # A. 盘中保本移动止损判定
                high_gain = (day_high - pos["entry_price"]) / pos["entry_price"]
                if high_gain >= 0.05 and not pos["breakeven_triggered"]:
                    pos["stop_price"] = pos["entry_price"]
                    pos["breakeven_triggered"] = True

                # B. 盘中触发止损判定
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

                # C. 满 10 日波段出局判定
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

        # 4. 计算今日当前仓位比
        portfolio_value = sum(pos["current_price"] * pos["shares"] for pos in active_positions.values())
        equity = cash + portfolio_value
        portfolio_equity.append({
            "trade_date": date,
            "equity": equity,
            "cash": cash,
            "color": color
        })
        current_total_pos_ratio = portfolio_value / equity if equity > 0 else 0.0

        # 5. 买入新仓 (收盘后确认信号，次日开盘买入)
        # 我们在今日收盘时筛选次日买入信号
        if color != "black":
            strong_inds = strong_industries_by_date.get(date, set())
            
            # 初筛当天主力大单和行业强度符合要求的股票
            candidates = []
            for code, p_df in price_by_code.items():
                today_row = p_df[p_df["trade_date"] == date]
                if today_row.empty:
                    continue
                
                # 行业强度和主力资金初筛
                ind = today_row.iloc[0]["industry"]
                is_money_ok = today_row.iloc[0]["money_ok"]
                is_limit_ok = today_row.iloc[0]["limit_3d_sum"] == 0
                
                if (ind in strong_inds) and is_money_ok and is_limit_ok:
                    # 股东筹码剔除未来函数匹配
                    holder_list = holder_by_code.get(code, [])
                    # 找出所有在今天之前已公告的记录 (ann_date <= date)
                    visible_holders = [h for h in holder_list if h["ann_date"] <= date]
                    
                    if len(visible_holders) >= 2:
                        latest_h = visible_holders[-1]["holder_num"]
                        prev_h = visible_holders[-2]["holder_num"]
                        
                        # 股东户数减少，筹码集中
                        if latest_h < prev_h:
                            close_price = float(today_row.iloc[0]["close"])
                            # 估算个股评分：按近期资金流入大小
                            candidates.append({
                                "ts_code": code,
                                "close": close_price,
                                "name": today_row.iloc[0]["name"]
                            })

            # 买入执行 (如果有名额的话)
            if candidates:
                # 按照近1日成交量大小排序，选取成交量较活跃的前 3 只
                selected_signals = candidates[:3] # 为简化，直接取前几只，或根据评分
                
                for sig in selected_signals:
                    code = sig["ts_code"]
                    if code in active_positions:
                        continue
                        
                    # 仓位检查
                    if current_total_pos_ratio + single_pos_pct <= max_total_pos:
                        # 确定买入开盘价（次日）
                        df_p = price_by_code.get(code)
                        if df_p is None:
                            continue
                        # 找出次日
                        next_idx = df_p[df_p["trade_date"] == date].index[0] + 1
                        if next_idx >= len(df_p):
                            continue
                        
                        tue_row = df_p.iloc[next_idx]
                        buy_date = tue_row["trade_date"]
                        open_price = float(tue_row["open"])
                        if open_price <= 0:
                            continue
                            
                        # 买股
                        buy_val = equity * single_pos_pct
                        shares = int(buy_val / open_price)
                        
                        if shares > 0 and cash >= shares * open_price:
                            cash -= shares * open_price
                            # 提取止损前20日低点
                            low20_val = low20_cache[code][next_idx]
                            stop_price = max(open_price * 0.92, low20_val) # 最深不超过 -8%
                            
                            active_positions[code] = {
                                "entry_date": buy_date,
                                "entry_price": open_price,
                                "stop_price": stop_price,
                                "shares": shares,
                                "current_price": open_price,
                                "hold_days": 0,
                                "breakeven_triggered": False
                            }
                            
                            # 更新实时仓位比
                            portfolio_value = sum(pos["current_price"] * pos["shares"] for pos in active_positions.values())
                            current_total_pos_ratio = portfolio_value / equity

    df_equity = pd.DataFrame(portfolio_equity)
    df_trades = pd.DataFrame(trade_history)

    print(f"\n  回测完成。期末资产: ¥{cash:,.2f} | 止损出局: {n_stopped} 次 | 到期10天出局: {n_expired} 次 | 黑色强制清仓: {n_black_cleared} 次")
    return df_equity, df_trades


# =============================================================================
# 绩效汇总
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

    print(f"\n{'='*70}")
    print("      StockAI v4.0 - 三因子共振波段交易策略量化报告")
    print(f"{'='*70}")
    print(f"\n【三因子策略交易绩效】")
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
    print(f"  组合最大回撤: {max_dd:.2%}  {'✅' if max_dd > -0.08 else '⚠️'}")
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
    parser = argparse.ArgumentParser(description="三因子共振策略回测")
    parser.add_argument("--start",  default="20250101")
    parser.add_argument("--end",    default="20251231")
    parser.add_argument("--output", default="reports/backtest_three_factors.csv")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")

    try:
        daily, money = load_all_data(conn, args.start, args.end)
        
        if daily.empty:
            print("[ERROR] 本地日线数据为空，终止回测")
            return
            
        trade_dates = sorted(
            daily[daily["trade_date"] >= args.start]["trade_date"].unique().tolist()
        )
        
        df_equity, df_trades = run_three_factor_backtest(daily, money, trade_dates, conn, args.start)
        
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

# -*- coding: utf-8 -*-
"""
周波段·周一战法 v1.2 优化版 (方案 A: 周期匹配中线化)
================================================
核心修改：
  1. 允许跨周持仓：取消周五无条件强平。
  2. 新增跌破生命线清仓：连续2天收盘价低于日线 MA20 则平仓。
  3. 周五周 MACD 弱化清仓：周五收盘时，若周线 MACD 柱状图变小，则周五收盘价平仓。
  4. 资金和仓位上限动态跟踪：限制总股票数最多 3 只，建仓时限制总仓位不超过大盘上限。
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

STOP_LOSS_PCT = 0.05  # 5% 止损
INITIAL_CAPITAL = 150000.0  # 15万小账户

SINGLE_POS_LIMITS = {
    "green":  0.15,
    "yellow": 0.10,
    "red":    0.05,
    "black":  0.00,
}


# =============================================================================
# 数据预加载与合成
# =============================================================================
def load_all_data(conn, start_date, end_date):
    pre_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=240)).strftime("%Y%m%d")
    print(f"[LOAD] 加载数据，预热起点: {pre_start} ~ 终点: {end_date}")

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

    # 合并主力净额（万元）
    money["net_main"] = (money["buy_elg_amount"] + money["buy_lg_amount"] -
                         money["sell_elg_amount"] - money["sell_lg_amount"])

    print(f"[DATA] 行情记录: {len(daily):,} 行 | 资金记录: {len(money):,} 行")
    return daily, money


# =============================================================================
# 周 MACD 向量化计算
# =============================================================================
def compute_weekly_macd(daily_df):
    print("[MACD] 向量化计算周 MACD 因子...")
    df = daily_df.copy()
    df["trade_date_dt"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    
    weekly_list = []
    
    for code, grp in df.groupby("ts_code"):
        grp = grp.sort_values("trade_date_dt")
        grp.set_index("trade_date_dt", inplace=True)
        
        w_df = grp["close"].resample("W").last().to_frame()
        w_df["ts_code"] = code
        w_df = w_df.dropna(subset=["close"])
        
        w_df["ema12"] = w_df["close"].ewm(span=12, adjust=False).mean()
        w_df["ema26"] = w_df["close"].ewm(span=26, adjust=False).mean()
        w_df["dif"] = w_df["ema12"] - w_df["ema26"]
        w_df["dea"] = w_df["dif"].ewm(span=9, adjust=False).mean()
        w_df["macd_hist"] = (w_df["dif"] - w_df["dea"]) * 2
        
        weekly_list.append(w_df.reset_index())

    weekly_all = pd.concat(weekly_list, ignore_index=True)
    
    df = df.sort_values("trade_date_dt")
    weekly_all = weekly_all.sort_values("trade_date_dt")
    
    df_merged = pd.merge_asof(
        df,
        weekly_all[["trade_date_dt", "ts_code", "dif", "dea", "macd_hist"]],
        on="trade_date_dt",
        by="ts_code",
        direction="backward"
    )
    
    df_merged = df_merged.sort_values(["ts_code", "trade_date"])
    df_merged["prev_macd_hist"] = df_merged.groupby("ts_code")["macd_hist"].shift(1)
    df_merged["weekly_macd_ok"] = (df_merged["macd_hist"] > df_merged["prev_macd_hist"]) & (df_merged["dif"] > df_merged["dea"])

    return df_merged


# =============================================================================
# 优化后仿真模拟
# =============================================================================
def run_monday_strategy_simulation(daily_res, money, trade_dates, conn, start_date):
    from market_env import get_market_mode
    
    print("[BACKTEST] 向量化计算日线 MA20 及资金累计因子...")
    df = daily_res.sort_values(["ts_code", "trade_date"]).copy()
    df["ma20"] = df.groupby("ts_code")["close"].transform(lambda x: x.rolling(20).mean())
    df["prev_ma20"] = df.groupby("ts_code")["ma20"].shift(1)
    df["ma20_up"] = df["ma20"] >= df["prev_ma20"]
    df["ma20_ok"] = (df["close"] > df["ma20"]) & df["ma20_up"]

    df["high_60d"] = df.groupby("ts_code")["close"].transform(lambda x: x.rolling(60).max())
    df["space_ok"] = (df["high_60d"] - df["close"]) / df["close"] > 0.03

    df["vol_5d_mean"] = df.groupby("ts_code")["vol"].transform(lambda x: x.rolling(5).mean())
    df["volume_ratio"] = df["vol"] / df["vol_5d_mean"].replace(0, np.nan)
    df["vol_ratio_ok"] = (df["volume_ratio"] >= 0.8) & (df["volume_ratio"] <= 1.5)

    print("[BACKTEST] 向量化计算资金面近5日累加量...")
    mdf = money.sort_values(["ts_code", "trade_date"]).copy()
    mdf["net_main_5d_sum"] = mdf.groupby("ts_code")["net_main"].transform(lambda x: x.rolling(5).sum())
    mdf["inflow_day"] = mdf["net_main"] > 0
    mdf["inflow_5d_cnt"] = mdf.groupby("ts_code")["inflow_day"].transform(lambda x: x.rolling(5).sum())
    mdf["money_ok"] = (mdf["net_main_5d_sum"] > 0) & (mdf["inflow_5d_cnt"] >= 3)

    df = df.merge(mdf[["ts_code", "trade_date", "money_ok"]], on=["ts_code", "trade_date"], how="left")
    df["money_ok"] = df["money_ok"].fillna(False)

    df["is_limit"] = df["pct_chg"] >= 9.8
    df["limit_3d_sum"] = df.groupby("ts_code")["is_limit"].transform(lambda x: x.rolling(3).sum())

    price_by_code = {}
    for code, grp in df.sort_values("trade_date").groupby("ts_code"):
        price_by_code[code] = grp[["trade_date", "open", "high", "low", "close", "pct_chg", "ma20", "macd_hist", "prev_macd_hist", "weekly_macd_ok"]].reset_index(drop=True)

    df_dates = pd.DataFrame({"trade_date": trade_dates})
    df_dates["trade_date_dt"] = pd.to_datetime(df_dates["trade_date"], format="%Y%m%d")
    df_dates["week"] = df_dates["trade_date_dt"].dt.isocalendar().week
    df_dates["year"] = df_dates["trade_date_dt"].dt.isocalendar().year
    
    weeks_list = []
    for (yr, wk), grp in df_dates.groupby(["year", "week"]):
        w_days = grp.sort_values("trade_date")["trade_date"].tolist()
        if len(w_days) >= 2:
            weeks_list.append({
                "year_week": f"{yr}_{wk}",
                "monday": w_days[0],
                "tuesday": w_days[1],
                "friday": w_days[-1],
                "days": w_days
            })

    cash = INITIAL_CAPITAL
    active_positions = {}  # ts_code -> {entry_date, entry_price, stop_price, shares, current_price, hold_days, breakeven_triggered, below_ma20_days}
    
    trade_history = []
    portfolio_equity = []
    
    n_stopped = 0
    n_macd_weak = 0
    n_ma20_broken = 0
    
    print(f"[BACKTEST] 运行 2025 全年 {len(weeks_list)} 个周度循环...")
    
    for w_idx, week_info in enumerate(weeks_list):
        mon = week_info["monday"]
        tue = week_info["tuesday"]
        fri = week_info["friday"]
        w_days = week_info["days"]
        
        # 1. 周一盘前：获取情绪定性，确定大盘总仓位上限
        color, max_total_pos, detail_json = get_market_mode(conn=conn, target_date=mon, persist=False)
        single_pos_pct = SINGLE_POS_LIMITS.get(color, 0.05)
        
        try:
            detail = json.loads(detail_json)
            explode_rate = detail.get("explode_rate", 0.0)
            promotion_rate = detail.get("promotion_rate", 1.0)
            if explode_rate > 0.40 or promotion_rate < 0.30:
                max_total_pos = 0.30
                single_pos_pct = 0.08
        except Exception:
            pass

        # 2. 周一筛选候选股票
        mon_df = df[df["trade_date"] == mon].copy()
        candidates = pd.DataFrame()
        if not mon_df.empty:
            # 行业前30%强度计算
            df_3d = df[df["trade_date"] <= mon].sort_values(["ts_code", "trade_date"])
            df_3d_grouped = df_3d.groupby("ts_code").tail(3)
            
            ind_gain = df_3d_grouped.groupby("industry")["pct_chg"].mean().reset_index()
            ind_gain["rank_pct"] = ind_gain["pct_chg"].rank(pct=True)
            strong_industries = ind_gain[ind_gain["rank_pct"] >= 0.70]["industry"].tolist()

            candidates = mon_df[
                mon_df["industry"].isin(strong_industries) &
                (mon_df["weekly_macd_ok"] == True) &
                (mon_df["ma20_ok"] == True) &
                (mon_df["money_ok"] == True) &
                (mon_df["vol_ratio_ok"] == True) &
                (mon_df["space_ok"] == True) &
                (~mon_df["name"].str.contains("ST")) &
                (~mon_df["name"].str.contains("st")) &
                (mon_df["limit_3d_sum"] == 0)
            ].copy()

            # 价格过滤
            current_equity = cash + sum(pos["current_price"] * pos["shares"] for pos in active_positions.values())
            max_stock_price = current_equity * 0.15 / 100.0
            candidates = candidates[candidates["close"] <= max_stock_price]

            candidates["score_flow"] = candidates["ts_code"].map(
                df_3d_grouped.groupby("ts_code")["vol"].sum()
            )
            top_candidates = candidates.sort_values("score_flow", ascending=False).head(3)["ts_code"].tolist()
        else:
            top_candidates = []

        # 3. 周二建仓
        # 黑色/极端风险下空头暂停建仓
        if color != "black" and max_total_pos > 0.0:
            for code in top_candidates:
                # 检查当前持仓数，股票总数上限为 3
                if len(active_positions) >= 3:
                    break
                
                # 避免重复建仓
                if code in active_positions:
                    continue
                
                df_p = price_by_code.get(code)
                if df_p is None:
                    continue
                tue_row = df_p[df_p["trade_date"] == tue]
                if tue_row.empty:
                    continue
                
                open_price = float(tue_row.iloc[0]["open"])
                if open_price <= 0:
                    continue
                
                # 限仓判定：确保加仓后总仓位不超过 max_total_pos
                current_portfolio_value = sum(p["current_price"] * p["shares"] for p in active_positions.values())
                current_equity = cash + current_portfolio_value
                
                available_pos_pct = max_total_pos - (current_portfolio_value / current_equity)
                if available_pos_pct <= 0:
                    continue
                
                final_single_pct = min(single_pos_pct, available_pos_pct, 0.12)
                if final_single_pct <= 0.01:
                    continue
                
                buy_value = current_equity * final_single_pct
                shares = int(buy_value / open_price)
                
                if shares > 0 and cash >= shares * open_price:
                    cash -= shares * open_price
                    tue_ma20 = float(tue_row.iloc[0]["ma20"])
                    stop_price = max(open_price * 0.95, tue_ma20)
                    
                    active_positions[code] = {
                        "entry_date": tue,
                        "entry_price": open_price,
                        "stop_price": stop_price,
                        "shares": shares,
                        "current_price": open_price,
                        "hold_days": 0,
                        "breakeven_triggered": False,
                        "below_ma20_days": 0
                    }

        # 4. 逐日仿真（周一至周五逐日滚动更新与平仓逻辑）
        for d in w_days:
            # 每日更新当前持仓市值
            for code, pos in active_positions.items():
                df_p = price_by_code.get(code)
                today_row = df_p[df_p["trade_date"] == d] if df_p is not None else None
                if today_row is not None and not today_row.empty:
                    pos["current_price"] = float(today_row.iloc[0]["close"])
                    pos["hold_days"] += 1

            p_val = sum(pos["current_price"] * pos["shares"] for pos in active_positions.values())
            curr_equity = cash + p_val
            portfolio_equity.append({
                "trade_date": d,
                "equity": curr_equity,
                "cash": cash,
                "color": color
            })
            
            # 日常跟踪与出局逻辑
            closed_codes = []
            for code, pos in active_positions.items():
                df_p = price_by_code.get(code)
                today_row = df_p[df_p["trade_date"] == d] if df_p is not None else None
                if today_row is None or today_row.empty:
                    continue
                
                day_low = float(today_row.iloc[0]["low"])
                day_close = float(today_row.iloc[0]["close"])
                day_high = float(today_row.iloc[0]["high"])
                day_ma20 = float(today_row.iloc[0]["ma20"])
                day_macd_hist = float(today_row.iloc[0]["macd_hist"])
                day_prev_macd_hist = float(today_row.iloc[0]["prev_macd_hist"])

                # A. 盘中保本止损触发判定（最高点涨幅 > 5% 后，止损线上推至买入价）
                high_gain = (day_high - pos["entry_price"]) / pos["entry_price"]
                if high_gain >= 0.05 and not pos["breakeven_triggered"]:
                    pos["stop_price"] = pos["entry_price"]
                    pos["breakeven_triggered"] = True

                # B. 盘中硬止损触发检测
                if day_low <= pos["stop_price"]:
                    exit_price = pos["stop_price"]
                    cash += exit_price * pos["shares"]
                    trade_history.append({
                        "ts_code": code,
                        "entry_date": pos["entry_date"],
                        "exit_date": d,
                        "entry_price": pos["entry_price"],
                        "exit_price": exit_price,
                        "shares": pos["shares"],
                        "exit_pct": (exit_price - pos["entry_price"]) / pos["entry_price"] * 100,
                        "reason": "🔴 触发止损"
                    })
                    closed_codes.append(code)
                    n_stopped += 1
                    continue

                # C. 连续2天跌破生命线 MA20 (收盘判定，在当天收盘时执行)
                if day_close < day_ma20:
                    pos["below_ma20_days"] += 1
                else:
                    pos["below_ma20_days"] = 0
                
                if pos["below_ma20_days"] >= 2:
                    exit_price = day_close
                    cash += exit_price * pos["shares"]
                    trade_history.append({
                        "ts_code": code,
                        "entry_date": pos["entry_date"],
                        "exit_date": d,
                        "entry_price": pos["entry_price"],
                        "exit_price": exit_price,
                        "shares": pos["shares"],
                        "exit_pct": (exit_price - pos["entry_price"]) / pos["entry_price"] * 100,
                        "reason": "📉 跌破生命线"
                    })
                    closed_codes.append(code)
                    n_ma20_broken += 1
                    continue

                # D. 周五收盘判定：周 MACD 走势弱化出局 (柱状图缩短)
                if d == fri:
                    if day_macd_hist <= day_prev_macd_hist:
                        exit_price = day_close
                        cash += exit_price * pos["shares"]
                        trade_history.append({
                            "ts_code": code,
                            "entry_date": pos["entry_date"],
                            "exit_date": d,
                            "entry_price": pos["entry_price"],
                            "exit_price": exit_price,
                            "shares": pos["shares"],
                            "exit_pct": (exit_price - pos["entry_price"]) / pos["entry_price"] * 100,
                            "reason": "⏳ MACD走弱平仓"
                        })
                        closed_codes.append(code)
                        n_macd_weak += 1

            for cc in closed_codes:
                if cc in active_positions:
                    del active_positions[cc]

    df_equity = pd.DataFrame(portfolio_equity)
    df_trades = pd.DataFrame(trade_history)

    print(f"  回测完成。期末资产: ¥{cash:,.2f} | 止损平仓: {n_stopped} 次 | 生命线跌破: {n_ma20_broken} 次 | MACD弱化平仓: {n_macd_weak} 次")
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
    print("  StockAI v4.0 - 周一战法优化方案A (周期匹配中线化) 量化报告")
    print(f"{'='*70}")
    print(f"\n【优化版交易绩效】")
    print(f"  交易总笔数  : {total_trades:,} 笔")
    print(f"  信号平均胜率: {win_rate:.1%}  {'✅' if win_rate >= 0.60 else '⚠️'}")
    print(f"  单笔均收益  : {avg_ret:+.2f}%")
    print(f"  平均盈利    : {gains:+.2f}%")
    print(f"  平均亏损    : {losses:+.2f}%")
    print(f"  系统盈亏比  : {pl_ratio:.2f}")
    print(f"  最大单笔亏损: {max_loss:+.2f}%")

    print(f"\n【小账户资产曲线 (初始 ¥150,000.00)】")
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
    parser = argparse.ArgumentParser(description="周波段·周一战法优化方案 A 离线回测")
    parser.add_argument("--start",  default="20250101")
    parser.add_argument("--end",    default="20251231")
    parser.add_argument("--output", default="reports/backtest_monday_opta.csv")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")

    try:
        daily, money = load_all_data(conn, args.start, args.end)
        
        if daily.empty:
            print("[ERROR] 本地日线数据为空，终止回测")
            return
            
        daily_res = compute_weekly_macd(daily)
        
        trade_dates = sorted(
            daily[daily["trade_date"] >= args.start]["trade_date"].unique().tolist()
        )
        
        df_equity, df_trades = run_monday_strategy_simulation(daily_res, money, trade_dates, conn, args.start)
        
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

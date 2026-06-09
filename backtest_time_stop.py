# -*- coding: utf-8 -*-
"""
时间止损回测 (backtest_time_stop.py)
====================================

P0 任务：验证"持仓5个交易日涨幅仍小于2%则强制平仓"规则

测试方案：
  1. 基准：无时间止损（持满10日或触发8%止损）
  2. 时间止损：持仓5日涨幅<2%强制平仓

全部其他条件与 v3.3 Final 保持一致：
  - 纯净双核心信号（主力+15 / 筹码+15，阈值≥30）
  - 微盘股过滤（流通市值<10亿排除）
  - 三重流出否决（主力+融资+北向同时流出→信号作废）
  - 固定8%止损
  - 最多持有 10 日

输出：时间止损 vs 基准对比表

用法：
    python backtest_time_stop.py --start 20250101 --end 20251231
"""

import os
import sys
import sqlite3
import argparse
import time
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")
sys.path.insert(0, ROOT_DIR)

SCORE_THRESHOLD  = 30     # 强信号门槛
MIN_CIRC_MV_YI   = 10.0  # 微盘股过滤阈值（亿元）
HOLD_DAYS        = 10     # 最长持有天数
STOP_PCT         = 0.08   # 8%固定止损
TIME_STOP_DAYS   = 5      # 时间止损天数
TIME_STOP_PCT    = 0.02   # 时间止损涨幅阈值


# =============================================================================
# 数据加载
# =============================================================================
def load_all_data(conn, start_date, end_date):
    pre_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")
    print(f"[START] 时间止损回测，区间: {start_date} ~ {end_date}")

    daily = pd.read_sql("""
        SELECT ts_code, trade_date, open, high, low, close, pct_chg, vol, amount
        FROM daily_prices WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(pre_start, end_date))

    money = pd.read_sql("""
        SELECT ts_code, trade_date,
               buy_elg_amount, sell_elg_amount,
               buy_lg_amount, sell_lg_amount
        FROM moneyflow WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(start_date, end_date))

    holder = pd.read_sql("""
        SELECT ts_code, ann_date, holder_num
        FROM stk_holdernumber ORDER BY ts_code, ann_date
    """, conn)

    try:
        circ_mv = pd.read_sql("""
            SELECT ts_code, MAX(trade_date) as ld, circ_mv
            FROM daily_basic WHERE circ_mv IS NOT NULL GROUP BY ts_code
        """, conn)
        circ_mv["circ_mv_yi"] = pd.to_numeric(circ_mv["circ_mv"], errors="coerce") / 10000.0
    except Exception:
        circ_mv = pd.DataFrame(columns=["ts_code", "circ_mv_yi"])

    try:
        margin = pd.read_sql("""
            SELECT ts_code, trade_date, rzye
            FROM margin_detail WHERE trade_date BETWEEN ? AND ?
            ORDER BY ts_code, trade_date
        """, conn, params=(start_date, end_date))
    except Exception:
        margin = pd.DataFrame(columns=["ts_code", "trade_date", "rzye"])

    try:
        hsgt = pd.read_sql("""
            SELECT trade_date, north_money FROM hsgt_moneyflow
            WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date
        """, conn, params=(start_date, end_date))
    except Exception:
        hsgt = pd.DataFrame(columns=["trade_date", "north_money"])

    print(f"[DATA] 日线:{len(daily):,} | 资金:{len(money):,} | 股东:{len(holder):,} | "
          f"市值:{len(circ_mv):,} | 融资:{len(margin):,} | 北向:{len(hsgt):,}")
    return daily, money, holder, circ_mv, margin, hsgt


# =============================================================================
# 信号生成
# =============================================================================
def compute_signals(daily_all, money, holder, circ_mv, margin, hsgt, start_date):
    daily = daily_all[daily_all["trade_date"] >= start_date].copy()

    money = money.copy()
    for c in ["buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount"]:
        money[c] = pd.to_numeric(money[c], errors="coerce").fillna(0)
    money["net_main"]  = (money["buy_elg_amount"] + money["buy_lg_amount"]
                          - money["sell_elg_amount"] - money["sell_lg_amount"])
    money["money_ok"]  = money["net_main"] > 0
    money["money_out"] = money["net_main"] < 0

    holder = holder.sort_values(["ts_code","ann_date"]).copy()
    holder["holder_num"]  = pd.to_numeric(holder["holder_num"], errors="coerce")
    holder["holder_prev"] = holder.groupby("ts_code")["holder_num"].shift(1)
    holder["holder_2d_ok"] = holder["holder_num"] < holder["holder_prev"]
    holder_latest = holder.groupby("ts_code").last().reset_index()

    if not margin.empty:
        margin = margin.sort_values(["ts_code","trade_date"]).copy()
        margin["rzye"] = pd.to_numeric(margin["rzye"], errors="coerce")
        margin["rzye_prev"] = margin.groupby("ts_code")["rzye"].shift(1)
        margin["margin_down"] = margin["rzye"] < margin["rzye_prev"]
        margin_latest = margin.sort_values(["ts_code","trade_date"]).groupby("ts_code").last().reset_index()
    else:
        margin_latest = pd.DataFrame(columns=["ts_code","margin_down"])

    if not hsgt.empty:
        hsgt = hsgt.sort_values("trade_date").copy()
        hsgt["north_money"] = pd.to_numeric(hsgt["north_money"], errors="coerce")
        hsgt["north_3d_mean"] = hsgt["north_money"].rolling(3).mean()
        hsgt["north_prev_3d_mean"] = hsgt["north_3d_mean"].shift(3)
        hsgt["hsgt_out"] = hsgt["north_3d_mean"] < hsgt["north_prev_3d_mean"]
        hsgt.loc[hsgt["north_prev_3d_mean"].isna(), "hsgt_out"] = \
            hsgt.loc[hsgt["north_prev_3d_mean"].isna(), "north_money"] < \
            hsgt.loc[hsgt["north_prev_3d_mean"].isna(), "north_money"].shift(1)
    else:
        hsgt["hsgt_out"] = False

    df = daily.merge(money[["ts_code","trade_date","money_ok","money_out"]],
                     on=["ts_code","trade_date"], how="left")
    df = df.merge(holder_latest[["ts_code","holder_2d_ok"]], on="ts_code", how="left")

    if not circ_mv.empty and "circ_mv_yi" in circ_mv.columns:
        valid = circ_mv[circ_mv["circ_mv_yi"] >= MIN_CIRC_MV_YI]["ts_code"]
        df = df[df["ts_code"].isin(valid)]

    if not margin_latest.empty:
        df = df.merge(margin_latest[["ts_code","margin_down"]], on="ts_code", how="left")
    else:
        df["margin_down"] = False

    if not hsgt.empty and "hsgt_out" in hsgt.columns:
        df = df.merge(hsgt[["trade_date","hsgt_out"]], on="trade_date", how="left")
    else:
        df["hsgt_out"] = False

    df["score"] = 0
    df.loc[df["money_ok"] == True, "score"] += 15
    df.loc[df["holder_2d_ok"] == True, "score"] += 15

    risk_flag = ((df["money_out"] == True) & (df["margin_down"] == True)
                 & (df["hsgt_out"] == True))
    df.loc[risk_flag, "score"] = 0

    df["signal"] = (df["score"] >= SCORE_THRESHOLD)
    print(f"[SIGNAL] 强信号: {df['signal'].sum():,} | 三重否决: {risk_flag.sum():,}")
    return df


# =============================================================================
# 回测核心
# =============================================================================
def run_backtest(daily_all, signals_df, enable_time_stop):
    """
    运行回测
    :param enable_time_stop: True=启用时间止损，False=基准（无时间止损）
    """
    strong = signals_df[signals_df["signal"] == True].copy()

    price_by_code = {}
    for code, grp in daily_all.sort_values("trade_date").groupby("ts_code"):
        price_by_code[code] = grp[["trade_date","low","close"]].reset_index(drop=True)

    def get_low20(code, entry_date):
        df = price_by_code.get(code)
        if df is None:
            return None
        past = df[df["trade_date"] <= entry_date].tail(20)
        if past.empty:
            return None
        return pd.to_numeric(past["low"], errors="coerce").min()

    results = []
    n_time_stopped = 0
    n_profit_after_stop = 0  # 被时间止损后继续反弹的数量
    total_time_stop_return = 0
    total_without_time_stop_return = 0

    for idx, (_, row) in enumerate(strong.iterrows()):
        if idx % 40000 == 0:
            mode = "时间止损" if enable_time_stop else "基准"
            print(f"    [{mode}] 进度: {idx:,}/{len(strong):,}")

        code        = row["ts_code"]
        entry_date  = row["trade_date"]
        entry_price = pd.to_numeric(row["close"], errors="coerce")
        if pd.isna(entry_price) or entry_price <= 0:
            continue

        low20 = get_low20(code, entry_date)
        stop_fixed   = entry_price * (1 - STOP_PCT)
        stop_struct  = (low20 * 0.98) if low20 and low20 > 0 else stop_fixed
        stop_price   = max(stop_fixed, stop_struct)

        price_df = price_by_code.get(code)
        if price_df is None:
            continue
        future = price_df[price_df["trade_date"] > entry_date].head(HOLD_DAYS).reset_index(drop=True)
        if len(future) < 1:
            continue

        exit_pct = None
        stopped  = False
        time_stopped = False
        
        # 记录5日和10日收盘价（用于分析时间止损后是否反弹）
        day5_close = None
        day10_close = None
        
        for day_idx, (_, fr) in enumerate(future.iterrows()):
            dl = pd.to_numeric(fr["low"],   errors="coerce")
            dc = pd.to_numeric(fr["close"], errors="coerce")
            
            # 记录特定天数的收盘价
            if day_idx == 4:  # 第5天（索引从0开始）
                day5_close = dc
            if day_idx == 9:  # 第10天
                day10_close = dc
            
            if pd.isna(dl):
                continue
            
            # 固定止损
            if dl <= stop_price:
                exit_pct = (stop_price - entry_price) / entry_price * 100
                stopped  = True
                break
            
            # 时间止损：持仓5日涨幅<2%强制平仓
            if enable_time_stop and day_idx == TIME_STOP_DAYS - 1:  # 第5天结束
                if dc is not None:
                    gain = (dc - entry_price) / entry_price
                    if gain < TIME_STOP_PCT:
                        exit_pct = gain * 100
                        stopped = True
                        time_stopped = True
                        n_time_stopped += 1
                        
                        # 检查如果继续持有到第10天会怎样
                        if day10_close is not None:
                            actual_return = (day10_close - entry_price) / entry_price * 100
                            total_without_time_stop_return += actual_return
                            if actual_return > 0:
                                n_profit_after_stop += 1
                        break

        if not stopped and len(future) >= 1:
            lc = pd.to_numeric(future.iloc[-1]["close"], errors="coerce")
            if not pd.isna(lc):
                exit_pct = (lc - entry_price) / entry_price * 100

        if exit_pct is not None:
            results.append({
                "exit_pct": exit_pct,
                "stopped": stopped,
                "time_stopped": time_stopped,
            })

    df_res = pd.DataFrame(results)
    stop_rate = sum(r["stopped"] for r in results) / len(results) if results else 0
    time_stop_rate = n_time_stopped / len(results) if results else 0
    avg_opportunity_cost = total_without_time_stop_return / n_time_stopped if n_time_stopped > 0 else 0
    
    return df_res, stop_rate, time_stop_rate, n_profit_after_stop, avg_opportunity_cost


# =============================================================================
# 统计分析
# =============================================================================
def calc_stats(df_res, stop_rate, time_stop_rate, n_profit_after_stop, avg_opportunity_cost, enable_time_stop):
    if df_res.empty:
        return {}
    
    win_rate = (df_res["exit_pct"] > 0).mean()
    avg_ret  = df_res["exit_pct"].mean()
    gains    = df_res.loc[df_res["exit_pct"] > 0, "exit_pct"].mean()
    losses   = df_res.loc[df_res["exit_pct"] < 0, "exit_pct"].mean()
    pl_ratio = abs(gains / losses) if losses and losses != 0 else float("nan")
    max_loss = df_res["exit_pct"].min()
    
    # 时间止损统计
    time_stopped_df = df_res[df_res["time_stopped"] == True]
    time_stop_win_rate = (time_stopped_df["exit_pct"] > 0).mean() if len(time_stopped_df) > 0 else 0
    time_stop_avg_ret = time_stopped_df["exit_pct"].mean() if len(time_stopped_df) > 0 else 0
    
    return {
        "name": "时间止损" if enable_time_stop else "基准",
        "n": len(df_res),
        "win_rate": round(win_rate, 4),
        "avg_ret": round(avg_ret, 3),
        "gains": round(gains, 3),
        "losses": round(losses, 3),
        "pl_ratio": round(pl_ratio, 3),
        "max_loss": round(max_loss, 3),
        "stop_rate": round(stop_rate, 4),
        "time_stop_rate": round(time_stop_rate, 4),
        "time_stop_win_rate": round(time_stop_win_rate, 4),
        "time_stop_avg_ret": round(time_stop_avg_ret, 3),
        "n_profit_after_stop": n_profit_after_stop,
        "avg_opportunity_cost": round(avg_opportunity_cost, 3),
    }


# =============================================================================
# 主程序
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="时间止损回测（P0任务）")
    parser.add_argument("--start",  default="20250101")
    parser.add_argument("--end",    default="20251231")
    parser.add_argument("--output", default="reports/time_stop_2025.csv")
    args = parser.parse_args()

    t0 = time.time()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")

    try:
        daily_all, money, holder, circ_mv, margin, hsgt = \
            load_all_data(conn, args.start, args.end)

        print("\n[SIGNAL] 生成强信号（含微盘股过滤+三重否决）...")
        signals_df = compute_signals(daily_all, money, holder, circ_mv, margin, hsgt, args.start)

        all_stats = []

        # 基准回测（无时间止损）
        print(f"\n{'─'*60}")
        print(f"[BACKTEST] 基准：无时间止损（持满10日或触发8%止损）")
        df_res, stop_rate, ts_rate, n_past, avg_opp = run_backtest(daily_all, signals_df, enable_time_stop=False)
        stat = calc_stats(df_res, stop_rate, ts_rate, n_past, avg_opp, enable_time_stop=False)
        if stat:
            all_stats.append(stat)

        # 时间止损回测
        print(f"\n{'─'*60}")
        print(f"[BACKTEST] 时间止损：持仓5日涨幅<2%强制平仓")
        df_res, stop_rate, ts_rate, n_past, avg_opp = run_backtest(daily_all, signals_df, enable_time_stop=True)
        stat = calc_stats(df_res, stop_rate, ts_rate, n_past, avg_opp, enable_time_stop=True)
        if stat:
            all_stats.append(stat)

        # ── 对比汇总表 ─────────────────────────────────────────────────────────
        print(f"\n\n{'='*72}")
        print("       P0 时间止损验证报告（v3.3 Final 数据，2025全年）")
        print(f"{'='*72}")
        print(f"  {'策略':>10} {'胜率':>8} {'均收益':>8} {'盈亏比':>7} {'最大亏损':>9} {'止损率':>8}")
        print(f"  {'─'*70}")

        for s in all_stats:
            print(f"  {s['name']:>10}  "
                  f"{s['win_rate']:>7.1%}  "
                  f"{s['avg_ret']:>+7.2f}%  "
                  f"{s['pl_ratio']:>7.2f}  "
                  f"{s['max_loss']:>+8.2f}%  "
                  f"{s['stop_rate']:>7.1%}")

        # 时间止损详细统计
        time_stat = all_stats[1]
        print(f"\n  【时间止损详细统计】")
        print(f"  ┌─────────────────────────────────────────────────────────┐")
        print(f"  │ 时间止损触发次数: {time_stat['n'] * time_stat['time_stop_rate']:,.0f} 次（占总交易 {time_stat['time_stop_rate']:.1%}）")
        print(f"  │ 时间止损胜率: {time_stat['time_stop_win_rate']:.1%}")
        print(f"  │ 时间止损均收益: {time_stat['time_stop_avg_ret']:+.2f}%")
        print(f"  │ 被时间止损后反弹的信号: {time_stat['n_profit_after_stop']} 次")
        print(f"  │ 平均机会成本（若继续持有）: {time_stat['avg_opportunity_cost']:+.2f}%")
        print(f"  └─────────────────────────────────────────────────────────┘")

        # 推荐方案
        print(f"\n  【Antigravity 推荐】")
        base_stat = all_stats[0]
        time_stat = all_stats[1]
        
        if time_stat["pl_ratio"] > base_stat["pl_ratio"] and time_stat["win_rate"] >= base_stat["win_rate"] * 0.95:
            print(f"  ✅ 建议启用时间止损规则：")
            print(f"     盈亏比 {time_stat['pl_ratio']:.2f} > 基准 {base_stat['pl_ratio']:.2f}")
            print(f"     胜率 {time_stat['win_rate']:.1%}（仅下降 {((base_stat['win_rate'] - time_stat['win_rate'])/base_stat['win_rate']*100):.0f}%）")
            print(f"     机会成本可控（平均 {time_stat['avg_opportunity_cost']:.2f}%）")
        else:
            print(f"  ⚠️  时间止损效果有限：")
            print(f"     盈亏比 {time_stat['pl_ratio']:.2f} vs 基准 {base_stat['pl_ratio']:.2f}")
            print(f"     胜率 {time_stat['win_rate']:.1%} vs 基准 {base_stat['win_rate']:.1%}")
            print(f"     建议作为可选规则，根据市场环境灵活启用")

        # 保存详细数据
        out_path = args.output if os.path.isabs(args.output) else os.path.join(ROOT_DIR, args.output)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        df_out = pd.DataFrame(all_stats)
        df_out.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n[OUTPUT] 结果已保存到 {out_path}")

    finally:
        conn.close()

    print(f"\n[DONE] 时间止损回测完成，耗时: {time.time()-t0:.2f} 秒")


if __name__ == "__main__":
    main()
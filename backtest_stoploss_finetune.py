# -*- coding: utf-8 -*-
"""
止损阈值精调回测 (backtest_stoploss_finetune.py)
================================================

P0 任务：在 v3.3 Final 全量数据基础上，对比三档止损阈值：
  5% → 当前参数（P/L 1.86，胜率 44.5%，触发率 40.9%）
  7% → 目标触发率~30%，预期胜率 47%+
  8% → 目标触发率~25%，预期胜率 50%+

全部其他条件与 v3.3 Final 保持完全一致：
  - 纯净双核心信号（主力+15 / 筹码+15，阈值≥30）
  - 微盘股过滤（流通市值<10亿排除）
  - 三重流出否决（主力+融资+北向同时流出→信号作废）
  - 最多持有 10 日，触止损即退出

输出：三档对比表 + 最优方案推荐

用法：
    python backtest_stoploss_finetune.py --start 20250101 --end 20251231
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

# 测试档位
STOP_LEVELS = [0.05, 0.07, 0.08]   # 5% / 7% / 8%


# =============================================================================
# 数据加载（与 backtest_final_v33 完全一致）
# =============================================================================
def load_all_data(conn, start_date, end_date):
    pre_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")
    print(f"[START] 止损精调回测，区间: {start_date} ~ {end_date}")
    print(f"[STOP]  测试档位: {[f'{s*100:.0f}%' for s in STOP_LEVELS]}")

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
# 信号生成（与 backtest_final_v33 完全一致）
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
        # P1 校准：用3日均值 vs 前3日均值，消除单日噪声
        hsgt["north_3d_mean"] = hsgt["north_money"].rolling(3).mean()
        hsgt["north_prev_3d_mean"] = hsgt["north_3d_mean"].shift(3)
        hsgt["hsgt_out"] = hsgt["north_3d_mean"] < hsgt["north_prev_3d_mean"]
        # 数据不足6条时，降级为日环比
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
        before = len(df)
        df = df[df["ts_code"].isin(valid)]
        print(f"[FILTER] 微盘股: {before:,} → {len(df):,} 条（剔除 {before-len(df):,} 条）")

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
# 单档止损回测核心
# =============================================================================
def run_single_stoploss(daily_all, signals_df, stop_pct, start_date):
    """对给定止损档位逐笔模拟。"""
    strong = signals_df[signals_df["signal"] == True].copy()

    # 预建价格索引
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
    n_stopped = 0

    for idx, (_, row) in enumerate(strong.iterrows()):
        if idx % 60000 == 0:
            print(f"    [{stop_pct*100:.0f}%] 进度: {idx:,}/{len(strong):,}")

        code        = row["ts_code"]
        entry_date  = row["trade_date"]
        entry_price = pd.to_numeric(row["close"], errors="coerce")
        if pd.isna(entry_price) or entry_price <= 0:
            continue

        # 止损线：固定N%止损 vs 结构止损，取较紧（较高）
        low20 = get_low20(code, entry_date)
        stop_fixed   = entry_price * (1 - stop_pct)
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
        for _, fr in future.iterrows():
            dl = pd.to_numeric(fr["low"],   errors="coerce")
            dc = pd.to_numeric(fr["close"], errors="coerce")
            if pd.isna(dl):
                continue
            if dl <= stop_price:
                exit_pct = (stop_price - entry_price) / entry_price * 100
                stopped  = True
                n_stopped += 1
                break

        if not stopped:
            lc = pd.to_numeric(future.iloc[-1]["close"], errors="coerce")
            if not pd.isna(lc):
                exit_pct = (lc - entry_price) / entry_price * 100

        if exit_pct is not None:
            results.append({
                "exit_pct": exit_pct,
                "stopped":  stopped,
            })

    df_res = pd.DataFrame(results)
    stop_rate = n_stopped / len(strong) if len(strong) > 0 else 0
    return df_res, stop_rate


# =============================================================================
# 统计分析
# =============================================================================
def calc_stats(df_res, stop_pct, stop_rate):
    if df_res.empty:
        return {}
    win_rate = (df_res["exit_pct"] > 0).mean()
    avg_ret  = df_res["exit_pct"].mean()
    gains    = df_res.loc[df_res["exit_pct"] > 0, "exit_pct"].mean()
    losses   = df_res.loc[df_res["exit_pct"] < 0, "exit_pct"].mean()
    pl_ratio = abs(gains / losses) if losses and losses != 0 else float("nan")
    max_loss = df_res["exit_pct"].min()
    return {
        "stop_pct":  stop_pct,
        "n":         len(df_res),
        "win_rate":  round(win_rate, 4),
        "avg_ret":   round(avg_ret, 3),
        "gains":     round(gains, 3),
        "losses":    round(losses, 3),
        "pl_ratio":  round(pl_ratio, 3),
        "max_loss":  round(max_loss, 3),
        "stop_rate": round(stop_rate, 4),
    }


# =============================================================================
# 主程序
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="止损阈值精调回测（P0任务）")
    parser.add_argument("--start",  default="20250101")
    parser.add_argument("--end",    default="20251231")
    parser.add_argument("--output", default="reports/stoploss_finetune_2025.csv")
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

        for sp in STOP_LEVELS:
            print(f"\n{'─'*60}")
            print(f"[BACKTEST] 止损档位: {sp*100:.0f}%")
            df_res, stop_rate = run_single_stoploss(daily_all, signals_df, sp, args.start)
            stat = calc_stats(df_res, sp, stop_rate)
            if stat:
                all_stats.append(stat)

        # ── 对比汇总表 ─────────────────────────────────────────────────────────
        print(f"\n\n{'='*72}")
        print("       P0 止损精调对比报告（v3.3 Final 数据，2025全年）")
        print(f"{'='*72}")
        print(f"  {'止损档位':>6} {'胜率':>8} {'均收益':>8} {'盈亏比':>7} {'平均盈利':>9} {'平均亏损':>9} {'最大亏损':>9} {'止损触发率':>10}")
        print(f"  {'─'*70}")

        best_pl = None
        best_win = None
        for s in all_stats:
            tag = ""
            if best_pl is None or s["pl_ratio"] > all_stats[
                    [i["stop_pct"] for i in all_stats].index(best_pl if best_pl else -1)
                ]["pl_ratio"] if best_pl else True:
                pass
            print(f"  {s['stop_pct']*100:>5.0f}%  "
                  f"{s['win_rate']:>7.1%}  "
                  f"{s['avg_ret']:>+7.2f}%  "
                  f"{s['pl_ratio']:>7.2f}  "
                  f"{s['gains']:>+8.2f}%  "
                  f"{s['losses']:>+8.2f}%  "
                  f"{s['max_loss']:>+8.2f}%  "
                  f"{s['stop_rate']:>9.1%}")

        # 找最优盈亏比和最优胜率
        best_pl_s  = max(all_stats, key=lambda x: x["pl_ratio"] if not pd.isna(x["pl_ratio"]) else 0)
        best_win_s = max(all_stats, key=lambda x: x["win_rate"])

        print(f"\n  ┌─────────────────────────────────────────────────────────┐")
        print(f"  │ 最优盈亏比: {best_pl_s['stop_pct']*100:.0f}% 止损 → P/L {best_pl_s['pl_ratio']:.2f} │ 胜率 {best_pl_s['win_rate']:.1%}    │")
        print(f"  │ 最优胜率  : {best_win_s['stop_pct']*100:.0f}% 止损 → 胜率 {best_win_s['win_rate']:.1%}  │ P/L {best_win_s['pl_ratio']:.2f}      │")
        print(f"  └─────────────────────────────────────────────────────────┘")

        # 推荐方案
        print(f"\n  【Antigravity 推荐】")
        # 以胜率≥50%且盈亏比最高为选择标准
        candidates = [s for s in all_stats if s["win_rate"] >= 0.50]
        if candidates:
            rec = max(candidates, key=lambda x: x["pl_ratio"])
            print(f"  ✅ 推荐 {rec['stop_pct']*100:.0f}% 止损：")
            print(f"     胜率 {rec['win_rate']:.1%} ≥ 50% ✅ | 盈亏比 {rec['pl_ratio']:.2f} | 触发率 {rec['stop_rate']:.1%}")
            print(f"\n  → 建议将 trade_plan.py 中的止损阈值从 5% 更新为 {rec['stop_pct']*100:.0f}%")
        else:
            # 全部胜率<50%，推荐盈亏比最高
            rec = best_pl_s
            print(f"  ⚠️  所有档位胜率未超过50%，推荐盈亏比最优方案：")
            print(f"     {rec['stop_pct']*100:.0f}% 止损 | 胜率 {rec['win_rate']:.1%} | 盈亏比 {rec['pl_ratio']:.2f}")

        # 保存详细数据
        out_path = args.output if os.path.isabs(args.output) else os.path.join(ROOT_DIR, args.output)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        df_out = pd.DataFrame(all_stats)
        df_out.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n[OUTPUT] 结果已保存到 {out_path}")

    finally:
        conn.close()

    print(f"\n[DONE] 止损精调回测完成，耗时: {time.time()-t0:.2f} 秒")


if __name__ == "__main__":
    main()

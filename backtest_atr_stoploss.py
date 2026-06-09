# -*- coding: utf-8 -*-
"""
ATR止损回测 (backtest_atr_stoploss.py)
======================================

P0 任务：验证动态ATR止损与固定8%止损对比

测试方案：
  1. 固定8%止损（当前最优）
  2. 2×ATR(14)动态止损
  3. 2.5×ATR(14)动态止损

全部其他条件与 v3.3 Final 保持一致：
  - 纯净双核心信号（主力+15 / 筹码+15，阈值≥30）
  - 微盘股过滤（流通市值<10亿排除）
  - 三重流出否决（主力+融资+北向同时流出→信号作废）
  - 最多持有 10 日

输出：三档止损对比表

用法：
    python backtest_atr_stoploss.py --start 20250101 --end 20251231
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
STOP_LEVELS = [
    {"name": "固定8%", "type": "fixed", "value": 0.08},
    {"name": "2×ATR(14)", "type": "atr", "multiplier": 2.0},
    {"name": "2.5×ATR(14)", "type": "atr", "multiplier": 2.5},
]


# =============================================================================
# 数据加载
# =============================================================================
def load_all_data(conn, start_date, end_date):
    pre_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")
    print(f"[START] ATR止损回测，区间: {start_date} ~ {end_date}")
    print(f"[STOP]  测试档位: {[s['name'] for s in STOP_LEVELS]}")

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
# ATR计算
# =============================================================================
def calculate_atr(df, period=14):
    """计算ATR(14)"""
    df = df.copy()
    df["high"] = pd.to_numeric(df["high"], errors="coerce")
    df["low"] = pd.to_numeric(df["low"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    
    # 计算True Range
    df["prev_close"] = df["close"].shift(1)
    df["tr1"] = df["high"] - df["low"]
    df["tr2"] = abs(df["high"] - df["prev_close"])
    df["tr3"] = abs(df["low"] - df["prev_close"])
    df["tr"] = df[["tr1", "tr2", "tr3"]].max(axis=1)
    
    # 计算ATR（简单移动平均）
    df["atr"] = df["tr"].rolling(period).mean()
    
    return df


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
        # P1 校准：用3日均值 vs 前3日均值
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
def run_single_stoploss(daily_all, signals_df, stop_config, start_date):
    """对给定止损档位逐笔模拟"""
    strong = signals_df[signals_df["signal"] == True].copy()

    # 预建价格索引（含ATR）
    price_by_code = {}
    for code, grp in daily_all.sort_values("trade_date").groupby("ts_code"):
        grp = calculate_atr(grp)
        price_by_code[code] = grp[["trade_date","low","close","atr"]].reset_index(drop=True)

    def get_stop_price(code, entry_date, entry_price, stop_config):
        """根据止损配置计算止损价"""
        df = price_by_code.get(code)
        if df is None:
            return entry_price * (1 - 0.08)  # 默认8%
        
        # 获取入场日的ATR
        entry_row = df[df["trade_date"] == entry_date]
        if entry_row.empty:
            return entry_price * (1 - 0.08)
        
        atr = entry_row.iloc[0]["atr"]
        if pd.isna(atr) or atr <= 0:
            return entry_price * (1 - 0.08)
        
        if stop_config["type"] == "fixed":
            return entry_price * (1 - stop_config["value"])
        elif stop_config["type"] == "atr":
            return entry_price - stop_config["multiplier"] * atr
        else:
            return entry_price * (1 - 0.08)

    results = []
    n_stopped = 0
    avg_stop_distance = 0

    for idx, (_, row) in enumerate(strong.iterrows()):
        if idx % 60000 == 0:
            print(f"    [{stop_config['name']}] 进度: {idx:,}/{len(strong):,}")

        code        = row["ts_code"]
        entry_date  = row["trade_date"]
        entry_price = pd.to_numeric(row["close"], errors="coerce")
        if pd.isna(entry_price) or entry_price <= 0:
            continue

        # 计算止损价
        stop_price = get_stop_price(code, entry_date, entry_price, stop_config)
        stop_distance = (entry_price - stop_price) / entry_price
        avg_stop_distance += stop_distance

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
                "stop_distance": stop_distance,
            })

    df_res = pd.DataFrame(results)
    stop_rate = n_stopped / len(strong) if len(strong) > 0 else 0
    avg_stop_distance = avg_stop_distance / len(results) if len(results) > 0 else 0
    return df_res, stop_rate, avg_stop_distance


# =============================================================================
# 统计分析
# =============================================================================
def calc_stats(df_res, stop_config, stop_rate, avg_stop_distance):
    if df_res.empty:
        return {}
    win_rate = (df_res["exit_pct"] > 0).mean()
    avg_ret  = df_res["exit_pct"].mean()
    gains    = df_res.loc[df_res["exit_pct"] > 0, "exit_pct"].mean()
    losses   = df_res.loc[df_res["exit_pct"] < 0, "exit_pct"].mean()
    pl_ratio = abs(gains / losses) if losses and losses != 0 else float("nan")
    max_loss = df_res["exit_pct"].min()
    return {
        "name": stop_config["name"],
        "type": stop_config["type"],
        "n": len(df_res),
        "win_rate": round(win_rate, 4),
        "avg_ret": round(avg_ret, 3),
        "gains": round(gains, 3),
        "losses": round(losses, 3),
        "pl_ratio": round(pl_ratio, 3),
        "max_loss": round(max_loss, 3),
        "stop_rate": round(stop_rate, 4),
        "avg_stop_distance": round(avg_stop_distance, 4),
    }


# =============================================================================
# 主程序
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="ATR止损回测（P0任务）")
    parser.add_argument("--start",  default="20250101")
    parser.add_argument("--end",    default="20251231")
    parser.add_argument("--output", default="reports/atr_stoploss_2025.csv")
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

        for sc in STOP_LEVELS:
            print(f"\n{'─'*60}")
            print(f"[BACKTEST] 止损档位: {sc['name']}")
            df_res, stop_rate, avg_stop_dist = run_single_stoploss(daily_all, signals_df, sc, args.start)
            stat = calc_stats(df_res, sc, stop_rate, avg_stop_dist)
            if stat:
                all_stats.append(stat)

        # ── 对比汇总表 ─────────────────────────────────────────────────────────
        print(f"\n\n{'='*72}")
        print("       P0 ATR止损对比报告（v3.3 Final 数据，2025全年）")
        print(f"{'='*72}")
        print(f"  {'止损类型':>12} {'胜率':>8} {'均收益':>8} {'盈亏比':>7} {'平均盈利':>9} "
              f"{'平均亏损':>9} {'最大亏损':>9} {'止损触发率':>10} {'平均止损距离':>12}")
        print(f"  {'─'*70}")

        for s in all_stats:
            print(f"  {s['name']:>12}  "
                  f"{s['win_rate']:>7.1%}  "
                  f"{s['avg_ret']:>+7.2f}%  "
                  f"{s['pl_ratio']:>7.2f}  "
                  f"{s['gains']:>+8.2f}%  "
                  f"{s['losses']:>+8.2f}%  "
                  f"{s['max_loss']:>+8.2f}%  "
                  f"{s['stop_rate']:>9.1%}  "
                  f"{s['avg_stop_distance']:>11.2%}")

        # 找最优方案
        best_pl_s  = max(all_stats, key=lambda x: x["pl_ratio"] if not pd.isna(x["pl_ratio"]) else 0)
        best_win_s = max(all_stats, key=lambda x: x["win_rate"])

        print(f"\n  ┌─────────────────────────────────────────────────────────┐")
        print(f"  │ 最优盈亏比: {best_pl_s['name']} → P/L {best_pl_s['pl_ratio']:.2f} │ 胜率 {best_pl_s['win_rate']:.1%}    │")
        print(f"  │ 最优胜率  : {best_win_s['name']} → 胜率 {best_win_s['win_rate']:.1%}  │ P/L {best_win_s['pl_ratio']:.2f}      │")
        print(f"  └─────────────────────────────────────────────────────────┘")

        # 推荐方案
        print(f"\n  【Antigravity 推荐】")
        # 比较ATR与固定止损
        fixed_stat = all_stats[0]  # 固定8%
        atr_stats = all_stats[1:]  # ATR止损
        
        better_stats = [s for s in atr_stats if s["pl_ratio"] > fixed_stat["pl_ratio"]]
        if better_stats:
            best_atr = max(better_stats, key=lambda x: x["pl_ratio"])
            print(f"  ✅ 推荐 {best_atr['name']} 动态止损：")
            print(f"     盈亏比 {best_atr['pl_ratio']:.2f} > 固定8%止损 {fixed_stat['pl_ratio']:.2f}")
            print(f"     平均止损距离 {best_atr['avg_stop_distance']:.2%}（动态适应波动）")
        else:
            print(f"  ✅ 推荐固定8%止损：")
            print(f"     盈亏比 {fixed_stat['pl_ratio']:.2f} ≥ ATR止损")
            print(f"     简单可靠，适合实盘执行")

        # 保存详细数据
        out_path = args.output if os.path.isabs(args.output) else os.path.join(ROOT_DIR, args.output)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        df_out = pd.DataFrame(all_stats)
        df_out.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n[OUTPUT] 结果已保存到 {out_path}")

    finally:
        conn.close()

    print(f"\n[DONE] ATR止损回测完成，耗时: {time.time()-t0:.2f} 秒")


if __name__ == "__main__":
    main()
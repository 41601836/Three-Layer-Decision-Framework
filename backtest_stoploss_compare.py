# -*- coding: utf-8 -*-
"""
止损方案对比回测脚本 (backtest_stoploss_compare.py)
===================================================

基于纯净双核心信号，对比三种止损方案：

  方案A：固定止损  — 买入后跌幅 > 5% 立即止损
  方案B：ATR动态止损 — 价格跌破 "买入价 - 2×ATR(14)" 时止损
  方案C：无止损基准 — 持有满10日（纯净基准线）

评价指标：
  - 10日胜率 / 均收益 / 盈亏比
  - 最大单笔亏损
  - 止损触发率

用法：
    python backtest_stoploss_compare.py --start 20250101 --end 20251231
"""

import os
import sys
import sqlite3
import argparse
import time
from datetime import datetime

import pandas as pd
import numpy as np

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")


# =============================================================================
# 数据加载
# =============================================================================

def load_all_data(conn, start_date, end_date):
    """批量加载全市场数据（含前60日用于ATR计算）"""
    # 提前60日用于计算ATR
    from datetime import datetime, timedelta
    dt_start = datetime.strptime(start_date, "%Y%m%d")
    pre_start = (dt_start - timedelta(days=90)).strftime("%Y%m%d")

    print(f"[START] 止损对比回测，区间: {start_date} ~ {end_date}")
    print("[LOAD] 正在加载日线数据（含前90日ATR窗口）...")
    daily = pd.read_sql("""
        SELECT ts_code, trade_date, open, high, low, close, pct_chg, vol, amount
        FROM daily_prices
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(pre_start, end_date))

    print("[LOAD] 正在加载资金流数据...")
    money = pd.read_sql("""
        SELECT ts_code, trade_date,
               buy_elg_amount, sell_elg_amount,
               buy_lg_amount, sell_lg_amount
        FROM moneyflow
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(start_date, end_date))

    print("[LOAD] 正在加载股东户数数据...")
    holder = pd.read_sql("""
        SELECT ts_code, ann_date, holder_num
        FROM stk_holdernumber
        ORDER BY ts_code, ann_date
    """, conn)

    print(f"[DATA] 日线: {len(daily):,} | 资金流: {len(money):,} | 股东: {len(holder):,}")
    return daily, money, holder, start_date


# =============================================================================
# ATR 计算
# =============================================================================

def compute_atr(df_stock, period=14):
    """
    计算 ATR(14)。
    df_stock: 单只股票的日线数据，按日期升序，含 high/low/close 列。
    返回添加了 atr 列的 DataFrame。
    """
    df = df_stock.copy().reset_index(drop=True)
    df["high"]  = pd.to_numeric(df["high"],  errors="coerce")
    df["low"]   = pd.to_numeric(df["low"],   errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")

    df["prev_close"] = df["close"].shift(1)
    df["tr"] = df[["high", "prev_close"]].max(axis=1) - df[["low", "prev_close"]].min(axis=1)
    df["atr"] = df["tr"].rolling(period, min_periods=1).mean()
    return df


# =============================================================================
# 信号生成（复用 backtest_pure_core 逻辑）
# =============================================================================

def generate_signals(daily_all, money, holder, start_date):
    """生成纯净双核心强信号（主力+筹码=30分）"""
    # 仅取回测区间内的日线
    daily = daily_all[daily_all["trade_date"] >= start_date].copy()

    print("[FACTOR] 计算主力资金净流入...")
    money = money.copy()
    for col in ["buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount"]:
        money[col] = pd.to_numeric(money[col], errors="coerce").fillna(0)
    money["net_main"] = (money["buy_elg_amount"] + money["buy_lg_amount"]
                         - money["sell_elg_amount"] - money["sell_lg_amount"])
    money["money_ok"] = money["net_main"] > 0

    print("[FACTOR] 计算股东户数连续下降...")
    holder = holder.sort_values(["ts_code", "ann_date"]).copy()
    holder["holder_num"]  = pd.to_numeric(holder["holder_num"], errors="coerce")
    holder["holder_prev"] = holder.groupby("ts_code")["holder_num"].shift(1)
    holder["holder_2d_ok"] = holder["holder_num"] < holder["holder_prev"]
    holder_latest = holder.sort_values(["ts_code", "ann_date"]).groupby("ts_code").last().reset_index()

    print("[SIGNAL] 合并数据...")
    df = daily.merge(money[["ts_code", "trade_date", "money_ok"]], on=["ts_code", "trade_date"], how="left")
    df = df.merge(holder_latest[["ts_code", "holder_2d_ok"]], on="ts_code", how="left")

    df["score"] = 0
    df.loc[df["money_ok"] == True, "score"] += 15
    df.loc[df["holder_2d_ok"] == True, "score"] += 15
    df["signal"] = (df["score"] == 30)

    n_strong = df["signal"].sum()
    print(f"[SIGNAL] 强信号: {n_strong:,} 条（{n_strong/len(df)*100:.1f}%）")
    return df


# =============================================================================
# 止损回测核心逻辑
# =============================================================================

def backtest_with_stoploss(daily_all, signals_df, scheme, start_date, end_date):
    """
    对强信号逐条模拟持有（最多10日），按止损方案决定实际持有收益。

    scheme: 'fixed'  — 固定5%止损
            'atr'    — ATR动态止损（2×ATR(14)）
            'none'   — 无止损，持满10日
    """
    print(f"\n[BACKTEST] 运行止损方案: {scheme.upper()}")

    # 只取强信号行
    sig_rows = signals_df[signals_df["signal"] == True].copy()

    # 预计算每只股票的 ATR（全区间）
    atr_map = {}
    if scheme == "atr":
        print("[ATR] 预计算 ATR(14)...")
        for code, grp in daily_all.sort_values("trade_date").groupby("ts_code"):
            atr_df = compute_atr(grp)
            atr_map[code] = dict(zip(atr_df["trade_date"], atr_df["atr"]))

    # 按股票分组，建立未来价格字典
    print("[PRICE] 建立未来价格索引...")
    price_by_code = {}
    for code, grp in daily_all.sort_values("trade_date").groupby("ts_code"):
        price_by_code[code] = grp[["trade_date", "open", "high", "low", "close", "pct_chg"]].reset_index(drop=True)

    results = []
    n_total = len(sig_rows)
    n_stopped = 0

    for idx, (_, row) in enumerate(sig_rows.iterrows()):
        if idx % 50000 == 0:
            print(f"  处理进度: {idx:,}/{n_total:,}")

        code        = row["ts_code"]
        entry_date  = row["trade_date"]
        entry_price = pd.to_numeric(row["close"], errors="coerce")

        if pd.isna(entry_price) or entry_price <= 0:
            continue

        # 获取该股票未来行情
        price_df = price_by_code.get(code)
        if price_df is None:
            continue
        future = price_df[price_df["trade_date"] > entry_date].head(10).reset_index(drop=True)
        if len(future) < 1:
            continue

        # ATR止损线
        if scheme == "atr":
            atr_val = atr_map.get(code, {}).get(entry_date, None)
            if atr_val is None or pd.isna(atr_val) or atr_val <= 0:
                atr_val = entry_price * 0.02  # 回退到2%
            stop_price_atr = entry_price - 2 * atr_val

        # 逐日模拟持有
        exit_pct = None
        stopped = False

        for i, frow in future.iterrows():
            day_low   = pd.to_numeric(frow["low"],   errors="coerce")
            day_close = pd.to_numeric(frow["close"], errors="coerce")

            if pd.isna(day_low) or pd.isna(day_close):
                continue

            if scheme == "fixed":
                # 固定5%止损：当日最低价触及止损线
                stop_price = entry_price * 0.95
                if day_low <= stop_price:
                    exit_pct = (stop_price - entry_price) / entry_price * 100
                    stopped  = True
                    n_stopped += 1
                    break

            elif scheme == "atr":
                if day_low <= stop_price_atr:
                    exit_pct = (stop_price_atr - entry_price) / entry_price * 100
                    stopped  = True
                    n_stopped += 1
                    break

        # 未触止损：以第10日（或最后一日）收盘价退出
        if not stopped:
            last_close = pd.to_numeric(future.iloc[-1]["close"], errors="coerce")
            if not pd.isna(last_close):
                exit_pct = (last_close - entry_price) / entry_price * 100

        if exit_pct is not None:
            results.append({
                "ts_code":   code,
                "entry_date": entry_date,
                "entry_price": entry_price,
                "exit_pct":  exit_pct,
                "stopped":   stopped,
            })

    df_res = pd.DataFrame(results)
    stop_rate = n_stopped / n_total if n_total > 0 else 0
    print(f"  止损触发: {n_stopped:,} 次（触发率 {stop_rate:.1%}）")
    return df_res


# =============================================================================
# 结果统计
# =============================================================================

def summarize(df_res, scheme_name):
    """计算并打印方案统计结果"""
    if df_res.empty:
        print(f"  [{scheme_name}] 无数据")
        return {}

    win_rate  = (df_res["exit_pct"] > 0).mean()
    avg_ret   = df_res["exit_pct"].mean()
    gains     = df_res.loc[df_res["exit_pct"] > 0, "exit_pct"].mean()
    losses    = df_res.loc[df_res["exit_pct"] < 0, "exit_pct"].mean()
    pl_ratio  = abs(gains / losses) if losses and losses != 0 else float("nan")
    max_loss  = df_res["exit_pct"].min()
    stop_rate = df_res["stopped"].mean() if "stopped" in df_res.columns else 0

    print(f"\n  ─── 方案: {scheme_name} ───────────────────────────────")
    print(f"  信号数量  : {len(df_res):,}")
    print(f"  胜率      : {win_rate:.1%}")
    print(f"  均收益    : {avg_ret:+.2f}%")
    print(f"  平均盈利  : {gains:+.2f}%")
    print(f"  平均亏损  : {losses:+.2f}%")
    print(f"  盈亏比    : {pl_ratio:.2f}")
    print(f"  最大单笔亏损: {max_loss:+.2f}%")
    print(f"  止损触发率: {stop_rate:.1%}")

    return {
        "scheme":    scheme_name,
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
    parser = argparse.ArgumentParser(description="止损方案对比回测")
    parser.add_argument("--start",  default="20250101", help="开始日期")
    parser.add_argument("--end",    default="20251231", help="结束日期")
    parser.add_argument("--output", help="输出CSV路径")
    args = parser.parse_args()

    t0 = time.time()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")

    try:
        daily_all, money, holder, start_date = load_all_data(conn, args.start, args.end)
        signals_df = generate_signals(daily_all, money, holder, start_date)

        stats_list = []
        all_results = {}

        for scheme, name in [
            ("none",  "C. 无止损基准（持满10日）"),
            ("fixed", "A. 固定止损5%"),
            ("atr",   "B. ATR动态止损(2×ATR14)"),
        ]:
            df_res = backtest_with_stoploss(daily_all, signals_df, scheme, args.start, args.end)
            stat   = summarize(df_res, name)
            if stat:
                stats_list.append(stat)
            all_results[scheme] = df_res

        # ── 对比总表 ──────────────────────────────────────────────────────────
        print(f"\n\n{'='*70}")
        print("       止损方案对比汇总")
        print(f"{'='*70}")
        print(f"{'方案':<30} {'胜率':>8} {'均收益':>8} {'盈亏比':>7} {'最大亏损':>9} {'止损率':>8}")
        print("-" * 70)

        best = None
        for s in stats_list:
            pl = s["pl_ratio"]
            if best is None or (not pd.isna(pl) and pl > stats_list[
                    stats_list.index(best)]["pl_ratio"]):
                best = s
            print(f"  {s['scheme']:<28} {s['win_rate']:>7.1%} {s['avg_ret']:>+7.2f}%"
                  f" {pl:>7.2f} {s['max_loss']:>+8.2f}% {s['stop_rate']:>7.1%}")

        print(f"\n{'='*70}")
        if best:
            print(f"  ✅ 最优方案（盈亏比最高）: {best['scheme']}")
            print(f"     胜率 {best['win_rate']:.1%} | 均收益 {best['avg_ret']:+.2f}% | 盈亏比 {best['pl_ratio']:.2f}")

        if args.output:
            # 合并三种方案结果并保存
            frames = []
            for scheme, df_r in all_results.items():
                df_r = df_r.copy()
                df_r["scheme"] = scheme
                frames.append(df_r)
            if frames:
                out_df = pd.concat(frames, ignore_index=True)
                out_path = args.output if os.path.isabs(args.output) else os.path.join(ROOT_DIR, args.output)
                out_df.to_csv(out_path, index=False, encoding="utf-8-sig")
                print(f"\n[OUTPUT] 结果已保存到 {out_path}")

    finally:
        conn.close()

    print(f"\n[DONE] 止损对比回测完成，耗时: {time.time()-t0:.2f} 秒")


if __name__ == "__main__":
    main()

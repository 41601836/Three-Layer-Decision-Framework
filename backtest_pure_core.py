# -*- coding: utf-8 -*-
"""
纯净核心回测脚本 (backtest_pure_core.py)
=========================================

目的：建立2025年策略真实基准线。

【纯净逻辑 - 仅双核心因子，无其他干扰】
  - 主力资金净流入 > 0 → +15分
  - 股东户数连续下降（2/3期）→ +15分
  - 无振幅加减分
  - 无三日背离
  - 无融资低位
  - 无任何其他辅助因子

信号门槛：
  强信号：总分 = 30（双核心全满）
  中信号：总分 = 15（单核心满足）
  无信号：总分 = 0

三重流出风险否决：
  主力资金流出 + 融资余额下降 + 北向资金流出 → 一票否决（总分清零）

此回测结果 = 双核心策略在2025年的真实表现基准。
用于与后续复杂策略对比，评估各辅助因子的边际贡献。

用法：
    python backtest_pure_core.py --start 20250101 --end 20251231
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
    """批量加载全市场数据"""
    print(f"[START] 纯净核心回测开始，区间: {start_date} ~ {end_date}")

    print("[LOAD] 正在加载日线数据...")
    daily = pd.read_sql("""
        SELECT ts_code, trade_date, open, high, low, close, pct_chg, vol, amount
        FROM daily_prices
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(start_date, end_date))

    print("[LOAD] 正在加载资金流数据...")
    money = pd.read_sql("""
        SELECT ts_code, trade_date,
               buy_elg_amount, sell_elg_amount,
               buy_lg_amount, sell_lg_amount,
               net_mf_amount
        FROM moneyflow
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(start_date, end_date))

    print("[LOAD] 正在加载股东户数数据...")
    holder = pd.read_sql("""
        SELECT ts_code, ann_date, end_date, holder_num
        FROM stk_holdernumber
        ORDER BY ts_code, ann_date
    """, conn)

    print("[LOAD] 正在加载融资融券数据...")
    margin = pd.read_sql("""
        SELECT ts_code, trade_date, rzye
        FROM margin_detail
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(start_date, end_date))

    print("[LOAD] 正在加载北向资金数据...")
    try:
        hsgt = pd.read_sql("""
            SELECT trade_date, north_money
            FROM hsgt_moneyflow
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY trade_date
        """, conn, params=(start_date, end_date))
        if hsgt.empty:
            print("  [WARN] hsgt_moneyflow 表无2025年数据，三重否决中北向条件将跳过")
    except Exception:
        hsgt = pd.DataFrame(columns=["trade_date", "north_money"])

    print(f"[DATA] 日线: {len(daily):,} | 资金流: {len(money):,} | "
          f"股东: {len(holder):,} | 融资: {len(margin):,} | 北向: {len(hsgt)}")

    return daily, money, holder, margin, hsgt


# =============================================================================
# 因子计算
# =============================================================================

def compute_factors(daily, money, holder, margin, hsgt):
    """纯净版：仅计算双核心因子 + 三重否决所需辅助数据"""

    # 主力资金净流入
    print("[FACTOR] 计算主力资金净流入...")
    money = money.copy()
    for col in ["buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount"]:
        money[col] = pd.to_numeric(money[col], errors="coerce").fillna(0)
    money["net_main"] = (money["buy_elg_amount"] + money["buy_lg_amount"]
                         - money["sell_elg_amount"] - money["sell_lg_amount"])
    money["money_ok"]  = money["net_main"] > 0
    money["money_out"] = money["net_main"] < 0

    # 股东户数连续下降（近2期）
    print("[FACTOR] 计算股东户数连续下降...")
    holder = holder.sort_values(["ts_code", "ann_date"]).copy()
    holder["holder_num"] = pd.to_numeric(holder["holder_num"], errors="coerce")
    holder["holder_prev"] = holder.groupby("ts_code")["holder_num"].shift(1)
    holder["holder_2d_ok"] = holder["holder_num"] < holder["holder_prev"]

    # 融资余额变化（用于三重否决）
    print("[FACTOR] 计算融资余额变化...")
    margin = margin.sort_values(["ts_code", "trade_date"]).copy()
    margin["rzye"] = pd.to_numeric(margin["rzye"], errors="coerce")
    margin["rzye_prev"] = margin.groupby("ts_code")["rzye"].shift(1)
    margin["margin_down"] = margin["rzye"] < margin["rzye_prev"]

    # 北向资金变化（用于三重否决）
    print("[FACTOR] 计算北向资金变化...")
    if not hsgt.empty:
        hsgt = hsgt.sort_values("trade_date").copy()
        hsgt["north_money"] = pd.to_numeric(hsgt["north_money"], errors="coerce")
        hsgt["north_prev"] = hsgt["north_money"].shift(1)
        hsgt["hsgt_out"] = hsgt["north_money"] < hsgt["north_prev"]
    else:
        hsgt["hsgt_out"] = False

    return daily, money, holder, margin, hsgt


# =============================================================================
# 信号生成（纯净双核心）
# =============================================================================

def generate_signals(daily, money, holder, margin, hsgt):
    """生成纯净核心信号：仅主力+筹码，无其他因子"""
    print("[SIGNAL] 合并数据...")

    df = daily.merge(money[["ts_code", "trade_date", "money_ok", "money_out"]],
                     on=["ts_code", "trade_date"], how="left")

    # 取每只股票最新的股东户数下降状态
    holder_latest = holder.sort_values(["ts_code", "ann_date"]).groupby("ts_code").last().reset_index()
    df = df.merge(holder_latest[["ts_code", "holder_2d_ok"]], on="ts_code", how="left")

    # 取每只股票最新融资状态
    margin_latest = margin.sort_values(["ts_code", "trade_date"]).groupby("ts_code").last().reset_index()
    df = df.merge(margin_latest[["ts_code", "margin_down"]], on="ts_code", how="left")

    # 北向资金
    if not hsgt.empty and "hsgt_out" in hsgt.columns:
        df = df.merge(hsgt[["trade_date", "hsgt_out"]], on="trade_date", how="left")
    else:
        df["hsgt_out"] = False

    print("[SIGNAL] 计算纯净双核心得分（主力+15，筹码+15）...")
    df["score_pure"] = 0
    df.loc[df["money_ok"] == True, "score_pure"] += 15   # 因子1：主力资金
    df.loc[df["holder_2d_ok"] == True, "score_pure"] += 15  # 因子2：筹码集中

    print("[SIGNAL] 三重流出风险否决...")
    risk_flag = (df["money_out"] == True) & (df["margin_down"] == True) & (df["hsgt_out"] == True)
    df.loc[risk_flag, "score_pure"] = 0

    df["signal"] = "none"
    df.loc[df["score_pure"] == 30, "signal"] = "strong"  # 双核心全满
    df.loc[df["score_pure"] == 15, "signal"] = "medium"  # 单核心满足

    print(f"[SIGNAL] 信号分布：strong={df['signal'].eq('strong').sum():,} | "
          f"medium={df['signal'].eq('medium').sum():,} | "
          f"none={df['signal'].eq('none').sum():,}")

    return df


# =============================================================================
# 未来收益计算
# =============================================================================

def compute_future_returns(df, periods=(5, 10, 20)):
    """计算未来N日累计收益"""
    print("[RETURN] 计算未来 N 日收益...")
    df = df.sort_values(["ts_code", "trade_date"]).copy()
    for p in periods:
        df[f"ret_{p}d"] = df.groupby("ts_code")["pct_chg"].transform(
            lambda x: x.shift(-1).rolling(p).sum()
        )
    return df


# =============================================================================
# 结果分析
# =============================================================================

def analyze_results(df, start_date, end_date):
    """统计分析回测结果"""
    df_valid = df.dropna(subset=["ret_10d"]).copy()

    print(f"\n{'='*70}")
    print(f"        纯净双核心回测结果 (主力+15 | 筹码+15 | 无其他因子)")
    print(f"{'='*70}")
    print(f"\n【回测概览】")
    print(f"  回测区间: {df['trade_date'].min()} ~ {df['trade_date'].max()}")
    print(f"  交易日数: {df['trade_date'].nunique()} 天")
    print(f"  总信号数: {len(df_valid):,} 条")

    print(f"\n【信号分布】")
    for sig in ["strong", "medium", "none"]:
        n = df_valid["signal"].eq(sig).sum()
        print(f"  {sig:8s}: {n:>8,} 条 ({n/len(df_valid)*100:.1f}%)")

    print(f"\n【核心指标（按信号强度）】")
    for sig in ["strong", "medium", "none"]:
        sub = df_valid[df_valid["signal"] == sig]
        if len(sub) < 10:
            continue
        for p in (5, 10, 20):
            col = f"ret_{p}d"
            if col not in sub.columns:
                continue
            wins = (sub[col] > 0).mean()
            avg  = sub[col].mean()
            gains = sub.loc[sub[col] > 0, col].mean()
            losses = sub.loc[sub[col] < 0, col].mean()
            pl_ratio = abs(gains / losses) if losses and losses != 0 else float("nan")
        # 只打印10日
        col = "ret_10d"
        wins  = (sub[col] > 0).mean()
        avg   = sub[col].mean()
        gains  = sub.loc[sub[col] > 0, col].mean() if (sub[col] > 0).any() else 0
        losses = sub.loc[sub[col] < 0, col].mean() if (sub[col] < 0).any() else -1
        pl_ratio = abs(gains / losses) if losses != 0 else float("nan")

        print(f"\n  {sig.upper()} 信号 ({len(sub):,} 条):")
        for p in (5, 10, 20):
            c = f"ret_{p}d"
            if c in sub.columns:
                w = (sub[c] > 0).mean()
                a = sub[c].mean()
                print(f"   {p:2d}日胜率: {w:.1%} | {p:2d}日均收益: {a:+.2f}%")
        print(f"    盈亏比: {pl_ratio:.2f}")

    print(f"\n【月度强信号表现】")
    strong = df_valid[df_valid["signal"] == "strong"].copy()
    if not strong.empty:
        strong["month"] = strong["trade_date"].str[:6]
        monthly = strong.groupby("month").agg(
            总信号=("ret_10d", "count"),
            均收益=("ret_10d", "mean"),
            胜率=("ret_10d", lambda x: (x > 0).mean() * 100)
        )
        print(monthly.to_string())

    print(f"\n【风险否决统计】")
    risk = (df["money_out"] == True) & (df["margin_down"] == True) & (df["hsgt_out"] == True)
    n_risk = risk.sum()
    print(f"  触发三重流出否决: {n_risk:,} 次")
    print(f"  否决占比: {n_risk/len(df)*100:.1f}%")

    return df_valid


# =============================================================================
# 主程序
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="纯净双核心回测（基准线建立）")
    parser.add_argument("--start", default="20250101", help="开始日期")
    parser.add_argument("--end",   default="20251231", help="结束日期")
    parser.add_argument("--output", help="输出结果到CSV文件")
    args = parser.parse_args()

    t0 = time.time()

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")

    try:
        daily, money, holder, margin, hsgt = load_all_data(conn, args.start, args.end)
        daily, money, holder, margin, hsgt = compute_factors(daily, money, holder, margin, hsgt)
        df_signal = generate_signals(daily, money, holder, margin, hsgt)
        df_result = compute_future_returns(df_signal)
        df_valid  = analyze_results(df_result, args.start, args.end)

        if args.output:
            out_path = args.output if os.path.isabs(args.output) else os.path.join(ROOT_DIR, args.output)
            df_valid.to_csv(out_path, index=False, encoding="utf-8-sig")
            print(f"\n[OUTPUT] 结果已保存到 {out_path}")

    finally:
        conn.close()

    print(f"\n[DONE] 纯净核心回测完成，耗时: {time.time()-t0:.2f} 秒")


if __name__ == "__main__":
    main()

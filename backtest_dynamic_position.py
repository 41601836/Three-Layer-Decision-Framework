# -*- coding: utf-8 -*-
"""
动态仓位回测 (backtest_dynamic_position.py)
============================================

P0 任务：验证动态仓位策略（进攻15%/防守5%/空仓0%）与固定仓位对比

测试方案：
  1. 固定仓位：强信号固定10%仓位
  2. 动态仓位：根据market_env判断，进攻15%/防守5%/空仓0%

全部其他条件与 v3.3 Final 保持一致：
  - 纯净双核心信号（主力+15 / 筹码+15，阈值≥30）
  - 微盘股过滤（流通市值<10亿排除）
  - 三重流出否决（主力+融资+北向同时流出→信号作废）
  - 8%固定止损（回测验证最优）
  - 最多持有 10 日

输出：动态仓位 vs 固定仓位对比表

用法：
    python backtest_dynamic_position.py --start 20250101 --end 20251231
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

# 动态仓位配置
POSITION_CONFIG = {
    "fixed": {"strong": 0.10, "medium": 0.05},  # 固定仓位
    "attack": {"strong": 0.15, "medium": 0.08},  # 进攻模式
    "defense": {"strong": 0.05, "medium": 0.02}, # 防守模式
    "empty": {"strong": 0.00, "medium": 0.00},   # 空仓模式
}


# =============================================================================
# 数据加载
# =============================================================================
def load_all_data(conn, start_date, end_date):
    pre_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")
    print(f"[START] 动态仓位回测，区间: {start_date} ~ {end_date}")

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

    # 加载上证指数用于判断市场模式
    try:
        index_df = pd.read_sql("""
            SELECT trade_date, close
            FROM daily_prices WHERE ts_code = '000001.SH'
            AND trade_date BETWEEN ? AND ?
            ORDER BY trade_date
        """, conn, params=(pre_start, end_date))
    except Exception:
        index_df = pd.DataFrame(columns=["trade_date", "close"])

    print(f"[DATA] 日线:{len(daily):,} | 资金:{len(money):,} | 股东:{len(holder):,} | "
          f"市值:{len(circ_mv):,} | 融资:{len(margin):,} | 北向:{len(hsgt):,} | 指数:{len(index_df):,}")
    return daily, money, holder, circ_mv, margin, hsgt, index_df


# =============================================================================
# 市场模式判断（简化版，用于回测）
# =============================================================================
def get_market_mode_for_date(index_df, trade_date):
    """根据上证指数判断当日市场模式"""
    if index_df.empty:
        return "defense"
    
    # 获取截止到trade_date的数据
    df = index_df[index_df["trade_date"] <= trade_date].copy()
    if len(df) < 62:
        return "defense"
    
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["ma60"] = df["close"].rolling(60).mean()
    
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    close_now = latest["close"]
    ma60_now = latest["ma60"]
    ma60_prev = prev["ma60"]
    
    if pd.isna(ma60_now) or pd.isna(ma60_prev) or ma60_prev == 0:
        return "defense"
    
    slope = (ma60_now - ma60_prev) / ma60_prev
    
    if close_now > ma60_now and slope > 0.002:
        return "attack"
    elif close_now < ma60_now and slope < -0.002:
        return "empty"
    else:
        return "defense"


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
# 单策略回测核心
# =============================================================================
def run_single_strategy(daily_all, signals_df, index_df, position_mode, start_date):
    """对给定仓位策略逐笔模拟"""
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
    total_position_used = 0
    max_drawdown = 0
    peak_value = 1.0  # 初始资产为1

    for idx, (_, row) in enumerate(strong.iterrows()):
        if idx % 60000 == 0:
            print(f"    [{position_mode}] 进度: {idx:,}/{len(strong):,}")

        code        = row["ts_code"]
        entry_date  = row["trade_date"]
        entry_price = pd.to_numeric(row["close"], errors="coerce")
        score       = row["score"]
        
        if pd.isna(entry_price) or entry_price <= 0:
            continue

        # 判断当日市场模式
        if position_mode == "dynamic":
            market_mode = get_market_mode_for_date(index_df, entry_date)
            pos_config = POSITION_CONFIG[market_mode]
        else:
            pos_config = POSITION_CONFIG["fixed"]
        
        # 根据得分确定仓位
        if score >= SCORE_THRESHOLD + 15:  # 强信号（>=45分）
            position_pct = pos_config["strong"]
        else:
            position_pct = pos_config["medium"]
        
        # 空仓模式跳过
        if position_pct == 0:
            continue
        
        total_position_used += position_pct

        # 止损线：固定8%止损
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
            # 计算加权收益
            weighted_return = exit_pct * position_pct
            results.append({
                "exit_pct": exit_pct,
                "stopped":  stopped,
                "position": position_pct,
                "weighted_return": weighted_return,
                "market_mode": market_mode if position_mode == "dynamic" else "fixed",
            })
            
            # 更新资产曲线（用于计算最大回撤）
            peak_value = max(peak_value, peak_value * (1 + weighted_return/100))
            current_value = peak_value * (1 + weighted_return/100)
            drawdown = (peak_value - current_value) / peak_value
            max_drawdown = max(max_drawdown, drawdown)

    df_res = pd.DataFrame(results)
    stop_rate = n_stopped / len(strong) if len(strong) > 0 else 0
    avg_position = total_position_used / len(results) if len(results) > 0 else 0
    return df_res, stop_rate, avg_position, max_drawdown


# =============================================================================
# 统计分析
# =============================================================================
def calc_stats(df_res, position_mode, stop_rate, avg_position, max_drawdown):
    if df_res.empty:
        return {}
    
    win_rate = (df_res["exit_pct"] > 0).mean()
    avg_ret  = df_res["exit_pct"].mean()
    weighted_avg_ret = df_res["weighted_return"].mean()
    gains    = df_res.loc[df_res["exit_pct"] > 0, "exit_pct"].mean()
    losses   = df_res.loc[df_res["exit_pct"] < 0, "exit_pct"].mean()
    pl_ratio = abs(gains / losses) if losses and losses != 0 else float("nan")
    max_loss = df_res["exit_pct"].min()
    
    # 按市场模式分组统计
    mode_stats = {}
    if "market_mode" in df_res.columns:
        for mode in df_res["market_mode"].unique():
            mode_df = df_res[df_res["market_mode"] == mode]
            mode_stats[mode] = {
                "count": len(mode_df),
                "win_rate": (mode_df["exit_pct"] > 0).mean(),
                "avg_ret": mode_df["exit_pct"].mean(),
            }
    
    return {
        "position_mode": position_mode,
        "n": len(df_res),
        "win_rate": round(win_rate, 4),
        "avg_ret": round(avg_ret, 3),
        "weighted_avg_ret": round(weighted_avg_ret, 4),
        "gains": round(gains, 3),
        "losses": round(losses, 3),
        "pl_ratio": round(pl_ratio, 3),
        "max_loss": round(max_loss, 3),
        "stop_rate": round(stop_rate, 4),
        "avg_position": round(avg_position, 4),
        "max_drawdown": round(max_drawdown, 4),
        "mode_stats": mode_stats,
    }


# =============================================================================
# 主程序
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="动态仓位回测（P0任务）")
    parser.add_argument("--start",  default="20250101")
    parser.add_argument("--end",    default="20251231")
    parser.add_argument("--output", default="reports/dynamic_position_2025.csv")
    args = parser.parse_args()

    t0 = time.time()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")

    try:
        daily_all, money, holder, circ_mv, margin, hsgt, index_df = \
            load_all_data(conn, args.start, args.end)

        print("\n[SIGNAL] 生成强信号（含微盘股过滤+三重否决）...")
        signals_df = compute_signals(daily_all, money, holder, circ_mv, margin, hsgt, args.start)

        all_stats = []

        # 测试固定仓位
        print(f"\n{'─'*60}")
        print(f"[BACKTEST] 固定仓位策略（强信号10%）")
        df_res, stop_rate, avg_pos, max_dd = run_single_strategy(
            daily_all, signals_df, index_df, "fixed", args.start)
        stat = calc_stats(df_res, "fixed", stop_rate, avg_pos, max_dd)
        if stat:
            all_stats.append(stat)

        # 测试动态仓位
        print(f"\n{'─'*60}")
        print(f"[BACKTEST] 动态仓位策略（进攻15%/防守5%/空仓0%）")
        df_res, stop_rate, avg_pos, max_dd = run_single_strategy(
            daily_all, signals_df, index_df, "dynamic", args.start)
        stat = calc_stats(df_res, "dynamic", stop_rate, avg_pos, max_dd)
        if stat:
            all_stats.append(stat)

        # ── 对比汇总表 ─────────────────────────────────────────────────────────
        print(f"\n\n{'='*72}")
        print("       P0 动态仓位对比报告（v3.3 Final 数据，2025全年）")
        print(f"{'='*72}")
        print(f"  {'策略':>10} {'胜率':>8} {'均收益':>8} {'加权收益':>10} {'盈亏比':>7} "
              f"{'平均仓位':>8} {'最大回撤':>10} {'止损触发率':>10}")
        print(f"  {'─'*70}")

        for s in all_stats:
            print(f"  {s['position_mode']:>10}  "
                  f"{s['win_rate']:>7.1%}  "
                  f"{s['avg_ret']:>+7.2f}%  "
                  f"{s['weighted_avg_ret']:>+9.4f}%  "
                  f"{s['pl_ratio']:>7.2f}  "
                  f"{s['avg_position']:>7.1%}  "
                  f"{s['max_drawdown']:>9.1%}  "
                  f"{s['stop_rate']:>9.1%}")

        # 动态仓位分模式统计
        dynamic_stat = all_stats[1] if len(all_stats) > 1 else {}
        if "mode_stats" in dynamic_stat:
            print(f"\n  【动态仓位分模式统计】")
            print(f"  {'模式':>10} {'交易次数':>10} {'胜率':>8} {'均收益':>8}")
            print(f"  {'─'*40}")
            for mode, ms in dynamic_stat["mode_stats"].items():
                mode_name = {"attack": "进攻", "defense": "防守", "empty": "空仓", "fixed": "固定"}.get(mode, mode)
                print(f"  {mode_name:>10}  {ms['count']:>10}  {ms['win_rate']:>7.1%}  {ms['avg_ret']:>+7.2f}%")

        # 推荐方案
        print(f"\n  【Antigravity 推荐】")
        fixed_stat = all_stats[0] if all_stats else {}
        dynamic_stat = all_stats[1] if len(all_stats) > 1 else {}
        
        if dynamic_stat.get("max_drawdown", 1) < fixed_stat.get("max_drawdown", 1):
            print(f"  ✅ 推荐动态仓位策略：")
            print(f"     最大回撤 {dynamic_stat['max_drawdown']:.1%} < 固定仓位 {fixed_stat['max_drawdown']:.1%}")
            print(f"     风险控制更优，建议启用")
        else:
            print(f"  ⚠️  固定仓位最大回撤更优：")
            print(f"     固定 {fixed_stat['max_drawdown']:.1%} vs 动态 {dynamic_stat['max_drawdown']:.1%}")
            print(f"     建议进一步优化市场模式判断逻辑")

        # 保存详细数据
        out_path = args.output if os.path.isabs(args.output) else os.path.join(ROOT_DIR, args.output)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        df_out = pd.DataFrame([{k: v for k, v in s.items() if k != "mode_stats"} for s in all_stats])
        df_out.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n[OUTPUT] 结果已保存到 {out_path}")

    finally:
        conn.close()

    print(f"\n[DONE] 动态仓位回测完成，耗时: {time.time()-t0:.2f} 秒")


if __name__ == "__main__":
    main()
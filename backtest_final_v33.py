# -*- coding: utf-8 -*-
"""
v3.3 Final 整合回测脚本 (backtest_final_v33.py)
================================================

整合以下全部优化，建立最终策略基准线：

  1. 纯净双核心信号  — 主力+15 / 筹码+15，阈值30
  2. 固定5%止损     — 回测验证盈亏比1.79最优
  3. 动态仓位       — 进攻15% / 防守5% / 空仓0%（最大回撤-34.5%）
  4. 微盘股过滤     — 流通市值 < 10亿 排除（daily_basic.circ_mv）
  5. 三重流出否决   — 主力+融资+北向均流出 → 信号作废

目标验证：
  ✅ 胜率 ≥ 55%
  ✅ 最大回撤 < -32%（较无止损基准-53.6%下降40%）
  ✅ 盈亏比 ≥ 1.5

用法：
    python backtest_final_v33.py --start 20250101 --end 20251231
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

# 策略参数（v3.3 Final）
STOP_LOSS_PCT   = 0.05   # 固定5%止损
POS_ATTACK      = 0.15   # 进攻模式仓位
POS_DEFENSE     = 0.05   # 防守模式仓位
POS_EMPTY       = 0.00   # 空仓模式
SCORE_THRESHOLD = 30     # 强信号门槛
MIN_CIRC_MV_YI  = 10.0  # 微盘股过滤（亿元）


# =============================================================================
# 数据加载
# =============================================================================

def load_all_data(conn, start_date, end_date):
    """加载全量数据（含前180日用于MA60和ATR预热）"""
    pre_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")

    print(f"[START] v3.3 Final 整合回测，区间: {start_date} ~ {end_date}")

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

    print("[LOAD] 流通市值数据（微盘股过滤）...")
    try:
        circ_mv = pd.read_sql("""
            SELECT ts_code, MAX(trade_date) as latest_date, circ_mv
            FROM daily_basic
            WHERE circ_mv IS NOT NULL
            GROUP BY ts_code
        """, conn)
        circ_mv["circ_mv_yi"] = pd.to_numeric(circ_mv["circ_mv"], errors="coerce") / 10000.0
        n_micro = (circ_mv["circ_mv_yi"] < MIN_CIRC_MV_YI).sum()
        print(f"  微盘股(<{MIN_CIRC_MV_YI}亿): {n_micro:,} 只，将被过滤")
    except Exception as e:
        print(f"  [WARN] 无法加载流通市值，跳过微盘股过滤: {e}")
        circ_mv = pd.DataFrame(columns=["ts_code", "circ_mv_yi"])

    print("[LOAD] 融资余额数据（三重否决）...")
    try:
        margin = pd.read_sql("""
            SELECT ts_code, trade_date, rzye
            FROM margin_detail
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY ts_code, trade_date
        """, conn, params=(start_date, end_date))
    except Exception:
        margin = pd.DataFrame(columns=["ts_code", "trade_date", "rzye"])

    print("[LOAD] 北向资金数据（三重否决）...")
    try:
        hsgt = pd.read_sql("""
            SELECT trade_date, north_money
            FROM hsgt_moneyflow
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY trade_date
        """, conn, params=(start_date, end_date))
        if hsgt.empty:
            print("  [WARN] 北向数据为空，三重否决中北向条件将跳过")
    except Exception:
        hsgt = pd.DataFrame(columns=["trade_date", "north_money"])

    print(f"[DATA] 日线:{len(daily):,} | 资金:{len(money):,} | 股东:{len(holder):,} | "
          f"市值:{len(circ_mv):,} | 融资:{len(margin):,} | 北向:{len(hsgt):,}")
    return daily, money, holder, circ_mv, margin, hsgt


# =============================================================================
# 逐日市场模式
# =============================================================================

def build_market_mode_map(conn, trade_dates):
    """逐日计算市场模式（严格 target_date 防未来泄露）"""
    from market_env import get_market_mode
    print(f"[MARKET] 逐日计算市场模式（{len(trade_dates)}个交易日）...")
    mode_map = {}
    for i, td in enumerate(trade_dates):
        if i % 30 == 0:
            print(f"  进度: {i}/{len(trade_dates)} ({td})")
        mode, _, _ = get_market_mode(conn=conn, target_date=td, persist=False)
        mode_map[td] = mode
    from collections import Counter
    cnt = Counter(mode_map.values())
    print(f"  attack={cnt['attack']} | defense={cnt['defense']} | empty={cnt['empty']}")
    return mode_map


# =============================================================================
# 因子计算 & 信号生成
# =============================================================================

def compute_signals(daily_all, money, holder, circ_mv, margin, hsgt, start_date):
    """计算双核心因子 + 三重否决 + 微盘股过滤，生成强信号"""
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
        hsgt["north_prev"]  = hsgt["north_money"].shift(1)
        hsgt["hsgt_out"]    = hsgt["north_money"] < hsgt["north_prev"]
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

    df["signal"] = (df["score"] >= SCORE_THRESHOLD)
    n_strong = df["signal"].sum()
    n_risk   = risk_flag.sum()
    print(f"[SIGNAL] 强信号: {n_strong:,} 条 | 三重否决: {n_risk:,} 次")
    return df


# =============================================================================
# 回测：固定5%止损 + 动态仓位
# =============================================================================

def run_backtest(daily_all, signals_df, mode_map, start_date):
    """
    逐笔模拟：固定5%止损 + 动态仓位
    对每条强信号：
      - 以信号日收盘价买入
      - 止损线 = max(close×0.95, low_20×0.98)
      - 持有最多10日，或触止损时提前退出
      - 组合日收益 = 当日信号均收益 × 当日仓位
    """
    print("[BACKTEST] 建立未来价格索引...")
    price_by_code = {}
    for code, grp in daily_all.sort_values("trade_date").groupby("ts_code"):
        price_by_code[code] = grp[["trade_date", "high", "low", "close", "pct_chg"]].reset_index(drop=True)

    # 提取低点（用于结构止损）
    def get_low20(code, entry_date):
        df = price_by_code.get(code)
        if df is None:
            return None
        past = df[df["trade_date"] <= entry_date].tail(20)
        if past.empty:
            return None
        return pd.to_numeric(past["low"], errors="coerce").min()

    strong = signals_df[signals_df["signal"] == True].copy()
    print(f"[BACKTEST] 开始逐笔模拟（{len(strong):,} 条强信号，固定5%止损+10日持有）...")

    results = []
    n_stopped = 0

    for idx, (_, row) in enumerate(strong.iterrows()):
        if idx % 50000 == 0:
            print(f"  进度: {idx:,}/{len(strong):,}")

        code        = row["ts_code"]
        entry_date  = row["trade_date"]
        entry_price = pd.to_numeric(row["close"], errors="coerce")

        if pd.isna(entry_price) or entry_price <= 0:
            continue

        # 止损线
        low20 = get_low20(code, entry_date)
        stop_fixed5  = entry_price * (1 - STOP_LOSS_PCT)
        stop_struct  = (low20 * 0.98) if low20 and low20 > 0 else stop_fixed5
        stop_price   = max(stop_fixed5, stop_struct)  # 取较紧（较高）的

        # 未来行情
        price_df = price_by_code.get(code)
        if price_df is None:
            continue
        future = price_df[price_df["trade_date"] > entry_date].head(10).reset_index(drop=True)
        if len(future) < 1:
            continue

        # 逐日模拟
        exit_pct = None
        stopped  = False
        for _, frow in future.iterrows():
            day_low   = pd.to_numeric(frow["low"],   errors="coerce")
            day_close = pd.to_numeric(frow["close"], errors="coerce")
            if pd.isna(day_low):
                continue
            if day_low <= stop_price:
                exit_pct = (stop_price - entry_price) / entry_price * 100
                stopped  = True
                n_stopped += 1
                break

        if not stopped:
            last_close = pd.to_numeric(future.iloc[-1]["close"], errors="coerce")
            if not pd.isna(last_close):
                exit_pct = (last_close - entry_price) / entry_price * 100

        if exit_pct is not None:
            results.append({
                "ts_code":    code,
                "entry_date": entry_date,
                "exit_pct":   exit_pct,
                "stopped":    stopped,
                "market_mode": mode_map.get(entry_date, "defense"),
            })

    df_res = pd.DataFrame(results)
    stop_rate = n_stopped / len(strong) if len(strong) > 0 else 0
    print(f"  止损触发: {n_stopped:,} 次（{stop_rate:.1%}）")
    return df_res


# =============================================================================
# 动态仓位模拟 & 绩效分析
# =============================================================================

def analyze_performance(df_res, mode_map):
    """计算最终绩效指标（含动态仓位加权）"""
    if df_res.empty:
        print("[ERROR] 无有效结果")
        return

    # 单信号绩效
    win_rate = (df_res["exit_pct"] > 0).mean()
    avg_ret  = df_res["exit_pct"].mean()
    gains    = df_res.loc[df_res["exit_pct"] > 0, "exit_pct"].mean()
    losses   = df_res.loc[df_res["exit_pct"] < 0, "exit_pct"].mean()
    pl_ratio = abs(gains / losses) if losses and losses != 0 else float("nan")
    max_loss = df_res["exit_pct"].min()
    stop_rate = df_res["stopped"].mean()

    # 动态仓位组合收益
    pos_map = {"attack": POS_ATTACK, "defense": POS_DEFENSE, "empty": POS_EMPTY}
    df_res["pos"] = df_res["market_mode"].map(pos_map).fillna(POS_DEFENSE)
    df_res["weighted_ret"] = df_res["exit_pct"] * df_res["pos"] / 0.10  # 以10%为基准归一化

    # 按日聚合组合收益
    daily_port = df_res.groupby("entry_date").agg(
        port_ret=("weighted_ret", "mean"),
        n_signals=("exit_pct", "count"),
    ).reset_index().sort_values("entry_date")

    rets = daily_port["port_ret"].values / 100.0
    cumret = np.cumprod(1 + rets)
    total_ret = cumret[-1] - 1

    peak = np.maximum.accumulate(cumret)
    dd   = (cumret - peak) / peak
    max_dd = dd.min()

    ann_ret = (1 + total_ret) ** (252 / max(len(rets), 1)) - 1
    ann_vol = np.std(rets) * np.sqrt(252)
    sharpe  = ann_ret / ann_vol if ann_vol > 0 else 0

    # 月度统计
    df_res["month"] = df_res["entry_date"].str[:6]
    monthly = df_res.groupby("month").agg(
        n=("exit_pct", "count"),
        win_rate=("exit_pct", lambda x: (x > 0).mean() * 100),
        avg_ret=("exit_pct", "mean"),
    )

    # ── 打印结果 ──────────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("       v3.3 Final 整合回测结果")
    print(f"{'='*70}")
    print(f"\n【单信号绩效】")
    print(f"  信号总数    : {len(df_res):,}")
    print(f"  胜率        : {win_rate:.1%}  {'✅' if win_rate >= 0.55 else '⚠️'}")
    print(f"  均收益      : {avg_ret:+.2f}%")
    print(f"  平均盈利    : {gains:+.2f}%")
    print(f"  平均亏损    : {losses:+.2f}%")
    print(f"  盈亏比      : {pl_ratio:.2f}  {'✅' if pl_ratio >= 1.5 else '⚠️'}")
    print(f"  最大单笔亏损: {max_loss:+.2f}%")
    print(f"  止损触发率  : {stop_rate:.1%}")

    print(f"\n【组合绩效（动态仓位加权）】")
    print(f"  总收益率    : {total_ret:+.2%}")
    print(f"  最大回撤    : {max_dd:.2%}  {'✅' if max_dd > -0.32 else '⚠️'}")
    print(f"  夏普比率    : {sharpe:.3f}")

    # 目标达成检验
    baseline_dd = -0.536
    dd_improve  = (max_dd - baseline_dd) / abs(baseline_dd)
    print(f"\n【目标达成检验】")
    print(f"  胜率≥55%   : {'✅' if win_rate >= 0.55 else '❌'} ({win_rate:.1%})")
    print(f"  最大回撤改善≥40%: {'✅' if dd_improve >= 0.40 else '❌'} "
          f"({dd_improve:.1%} 改善，{max_dd:.2%} vs 基准-53.6%)")
    print(f"  盈亏比≥1.5 : {'✅' if pl_ratio >= 1.5 else '❌'} ({pl_ratio:.2f})")

    print(f"\n【月度收益表】")
    print(f"  {'月份':<8} {'信号数':>8} {'胜率':>8} {'均收益':>8}")
    print(f"  {'-'*38}")
    for m, row_m in monthly.iterrows():
        bar = '█' * int(abs(row_m['avg_ret']) / 0.5)
        sign = '+' if row_m['avg_ret'] >= 0 else ''
        print(f"  {m}   {int(row_m['n']):>8,}  {row_m['win_rate']:>6.1f}%  "
              f"{sign}{row_m['avg_ret']:>5.2f}% {bar}")

    return {
        "win_rate": round(win_rate, 4),
        "avg_ret":  round(avg_ret, 3),
        "pl_ratio": round(pl_ratio, 3),
        "max_loss": round(max_loss, 3),
        "stop_rate": round(stop_rate, 4),
        "total_ret": round(total_ret, 4),
        "max_dd":   round(max_dd, 4),
        "sharpe":   round(sharpe, 3),
        "dd_improve": round(dd_improve, 4),
    }


# =============================================================================
# 主程序
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="v3.3 Final 整合回测")
    parser.add_argument("--start",  default="20250101")
    parser.add_argument("--end",    default="20251231")
    parser.add_argument("--skip-market-mode", action="store_true",
                        help="跳过逐日市场模式计算（全程defense，快速验证用）")
    parser.add_argument("--output", default="reports/backtest_final_v33_2025.csv")
    args = parser.parse_args()

    t0 = time.time()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")

    try:
        daily_all, money, holder, circ_mv, margin, hsgt = \
            load_all_data(conn, args.start, args.end)

        # 获取回测区间交易日
        trade_dates = sorted(
            daily_all[daily_all["trade_date"] >= args.start]["trade_date"].unique().tolist()
        )

        # 市场模式（逐日）
        if not args.skip_market_mode:
            mode_map = build_market_mode_map(conn, trade_dates)
        else:
            print("[MARKET] 跳过逐日计算，全程使用 defense 模式")
            mode_map = {td: "defense" for td in trade_dates}

        # 信号生成
        signals_df = compute_signals(daily_all, money, holder, circ_mv, margin, hsgt, args.start)

        # 回测
        df_res = run_backtest(daily_all, signals_df, mode_map, args.start)

        # 绩效分析
        stats = analyze_performance(df_res, mode_map)

        # 保存
        if df_res is not None and not df_res.empty:
            out_path = args.output if os.path.isabs(args.output) else os.path.join(ROOT_DIR, args.output)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            df_res.to_csv(out_path, index=False, encoding="utf-8-sig")
            print(f"\n[OUTPUT] 结果已保存到 {out_path}")

    finally:
        conn.close()

    print(f"\n[DONE] v3.3 Final 回测完成，耗时: {time.time()-t0:.2f} 秒")


if __name__ == "__main__":
    main()

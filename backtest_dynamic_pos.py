# -*- coding: utf-8 -*-
"""
动态仓位方案回测脚本 (backtest_dynamic_pos.py)
===============================================

基于纯净双核心信号 + market_env 市场模式，对比：
  方案A：固定仓位  — 每次信号统一 10% 仓位
  方案B：动态仓位  — 进攻模式15% / 防守模式5% / 空仓模式0%

评价维度：
  - 组合累计收益曲线
  - 最大回撤（按日收益序列计算）
  - 月度收益平滑度（标准差）
  - 夏普比率（近似）

市场模式规则（来自 market_env.get_market_mode）：
  上证指数 > MA60 且斜率 > +0.2% → attack（进攻）
  上证指数 < MA60 且斜率 < -0.2% → empty（空仓）
  其余                            → defense（防守）

注意：回测中 market_env 调用时传入 target_date 参数，严格防止未来数据。

用法：
    python backtest_dynamic_pos.py --start 20250101 --end 20251231
"""

import os
import sys
import sqlite3
import argparse
import time

import pandas as pd
import numpy as np

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")
sys.path.insert(0, ROOT_DIR)


# 仓位配置
POS_FIXED   = 0.10   # 固定仓位
POS_ATTACK  = 0.15   # 进攻模式
POS_DEFENSE = 0.05   # 防守模式
POS_EMPTY   = 0.00   # 空仓模式


# =============================================================================
# 数据加载
# =============================================================================

def load_all_data(conn, start_date, end_date):
    """加载回测所需全量数据"""
    print(f"[START] 动态仓位回测，区间: {start_date} ~ {end_date}")

    # 前120日用于MA60计算
    from datetime import datetime, timedelta
    pre_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")

    print("[LOAD] 日线数据（含前120日MA60窗口）...")
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

    print(f"[DATA] 日线: {len(daily):,} | 资金流: {len(money):,} | 股东: {len(holder):,}")
    return daily, money, holder


# =============================================================================
# 每日市场模式（回测用，严格截止 target_date）
# =============================================================================

def build_daily_market_mode(conn, trade_dates):
    """
    为回测区间内的每个交易日计算市场模式。
    使用 market_env._mode_from_index / _mode_from_market_breadth，
    传入 target_date 防止未来泄露。
    """
    print("[MARKET] 逐日计算市场模式...")
    from market_env import get_market_mode

    mode_map = {}
    for i, td in enumerate(trade_dates):
        if i % 20 == 0:
            print(f"  进度: {i}/{len(trade_dates)} ({td})")
        mode, _, _ = get_market_mode(
            conn=conn,
            target_date=td,
            persist=False     # 回测不写 JSON 文件
        )
        mode_map[td] = mode

    # 统计
    from collections import Counter
    cnt = Counter(mode_map.values())
    print(f"  attack={cnt['attack']} | defense={cnt['defense']} | empty={cnt['empty']}")
    return mode_map


# =============================================================================
# 信号生成（纯净双核心，复用逻辑）
# =============================================================================

def generate_signals(daily_all, money, holder, start_date):
    """生成纯净双核心强信号"""
    daily = daily_all[daily_all["trade_date"] >= start_date].copy()

    money = money.copy()
    for col in ["buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount"]:
        money[col] = pd.to_numeric(money[col], errors="coerce").fillna(0)
    money["net_main"] = (money["buy_elg_amount"] + money["buy_lg_amount"]
                         - money["sell_elg_amount"] - money["sell_lg_amount"])
    money["money_ok"] = money["net_main"] > 0

    holder = holder.sort_values(["ts_code", "ann_date"]).copy()
    holder["holder_num"]  = pd.to_numeric(holder["holder_num"], errors="coerce")
    holder["holder_prev"] = holder.groupby("ts_code")["holder_num"].shift(1)
    holder["holder_2d_ok"] = holder["holder_num"] < holder["holder_prev"]
    holder_latest = holder.sort_values(["ts_code", "ann_date"]).groupby("ts_code").last().reset_index()

    df = daily.merge(money[["ts_code", "trade_date", "money_ok"]], on=["ts_code", "trade_date"], how="left")
    df = df.merge(holder_latest[["ts_code", "holder_2d_ok"]], on="ts_code", how="left")

    df["score"] = 0
    df.loc[df["money_ok"] == True, "score"] += 15
    df.loc[df["holder_2d_ok"] == True, "score"] += 15
    df["signal"] = (df["score"] == 30)

    print(f"[SIGNAL] 强信号: {df['signal'].sum():,} 条")
    return df


# =============================================================================
# 计算未来10日收益（用于两种仓位方案的相同信号）
# =============================================================================

def compute_future_returns(signals_df):
    """计算每条信号的未来10日累计收益"""
    df = signals_df.sort_values(["ts_code", "trade_date"]).copy()
    df["ret_10d"] = df.groupby("ts_code")["pct_chg"].transform(
        lambda x: x.shift(-1).rolling(10).sum()
    )
    return df


# =============================================================================
# 模拟组合收益
# =============================================================================

def simulate_portfolio(df_signals, mode_map, pos_scheme, start_date, end_date):
    """
    模拟组合日收益。

    pos_scheme: 'fixed' 或 'dynamic'
    思路：
      - 每个交易日，统计当日有强信号的股票数量 n
      - 按 pos_scheme 分配每只股票仓位，总仓位 = 单股仓位 × n（上限100%）
      - 组合当日收益 = 平均收益 × 总仓位（简化：等权持有）
      - 无信号日：收益 = 0（空仓）
    """
    strong = df_signals[df_signals["signal"] == True].copy()
    strong = strong.dropna(subset=["ret_10d"])

    # 按日聚合
    daily_stats = strong.groupby("trade_date").agg(
        n_signals=("ret_10d", "count"),
        avg_ret=("ret_10d", "mean"),
    ).reset_index()

    rows = []
    for _, row in daily_stats.iterrows():
        td = row["trade_date"]
        n  = int(row["n_signals"])
        avg_ret = row["avg_ret"]

        if pos_scheme == "fixed":
            pos_per_stock = POS_FIXED
        else:
            mode = mode_map.get(td, "defense")
            if mode == "attack":
                pos_per_stock = POS_ATTACK
            elif mode == "empty":
                pos_per_stock = POS_EMPTY
            else:
                pos_per_stock = POS_DEFENSE

        # 总仓位（等权，上限100%）
        total_pos = min(pos_per_stock * n, 1.0) if n > 0 else 0

        # 组合当日贡献收益
        day_return = avg_ret * (pos_per_stock / POS_FIXED) if pos_scheme == "fixed" else avg_ret
        portfolio_return = avg_ret * total_pos / 1.0  # 归一化仓位收益

        rows.append({
            "trade_date":      td,
            "n_signals":       n,
            "pos_per_stock":   pos_per_stock,
            "total_pos":       total_pos,
            "avg_ret":         avg_ret,
            "portfolio_return": portfolio_return,
            "market_mode":     mode_map.get(td, "defense") if pos_scheme == "dynamic" else "fixed",
        })

    df_port = pd.DataFrame(rows).sort_values("trade_date")
    return df_port


# =============================================================================
# 绩效指标计算
# =============================================================================

def calc_performance(df_port, scheme_name):
    """计算组合绩效指标"""
    rets = df_port["portfolio_return"].values / 100.0  # 转为小数

    # 累计净值
    cumret = np.cumprod(1 + rets)
    total_ret = cumret[-1] - 1

    # 最大回撤
    peak = np.maximum.accumulate(cumret)
    drawdown = (cumret - peak) / peak
    max_dd = drawdown.min()

    # 月度收益
    df_port = df_port.copy()
    df_port["month"] = df_port["trade_date"].str[:6]
    monthly = df_port.groupby("month")["portfolio_return"].mean()
    monthly_std = monthly.std()

    # 夏普（简化：年化收益/年化波动）
    ann_ret = (1 + total_ret) ** (252 / max(len(rets), 1)) - 1
    ann_vol = np.std(rets) * np.sqrt(252)
    sharpe  = ann_ret / ann_vol if ann_vol > 0 else 0

    print(f"\n  ─── 方案: {scheme_name} ───────────────────────────────")
    print(f"  总收益率  : {total_ret:+.2%}")
    print(f"  最大回撤  : {max_dd:.2%}")
    print(f"  夏普比率  : {sharpe:.2f}")
    print(f"  月收益标准差: {monthly_std:.2f}%（越小越平滑）")
    print(f"\n  月度收益：")
    for m, v in monthly.items():
        bar = "█" * int(abs(v) / 0.5) if not pd.isna(v) else ""
        sign = "+" if v >= 0 else ""
        print(f"    {m}: {sign}{v:.2f}% {bar}")

    return {
        "scheme":      scheme_name,
        "total_ret":   round(total_ret, 4),
        "max_dd":      round(max_dd, 4),
        "sharpe":      round(sharpe, 3),
        "monthly_std": round(monthly_std, 3),
    }


# =============================================================================
# 主程序
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="动态仓位 vs 固定仓位回测")
    parser.add_argument("--start",  default="20250101", help="开始日期")
    parser.add_argument("--end",    default="20251231", help="结束日期")
    parser.add_argument("--skip-market-mode", action="store_true",
                        help="跳过逐日市场模式计算（用历史结果快速测试）")
    parser.add_argument("--output", help="输出CSV路径")
    args = parser.parse_args()

    t0 = time.time()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")

    try:
        daily_all, money, holder = load_all_data(conn, args.start, args.end)

        # 获取回测区间交易日列表
        trade_dates = sorted(
            daily_all[daily_all["trade_date"] >= args.start]["trade_date"].unique().tolist()
        )

        # 逐日市场模式
        if not args.skip_market_mode:
            mode_map = build_daily_market_mode(conn, trade_dates)
        else:
            print("[MARKET] 跳过逐日计算，默认全部 defense 模式")
            mode_map = {td: "defense" for td in trade_dates}

        # 信号生成
        signals_df = generate_signals(daily_all, money, holder, args.start)
        signals_df = compute_future_returns(signals_df)

        # 方案A：固定仓位
        df_fixed   = simulate_portfolio(signals_df, mode_map, "fixed",   args.start, args.end)
        # 方案B：动态仓位
        df_dynamic = simulate_portfolio(signals_df, mode_map, "dynamic", args.start, args.end)

        # 绩效对比
        print(f"\n{'='*70}")
        print("       动态仓位 vs 固定仓位 回测结果")
        print(f"{'='*70}")

        stat_fixed   = calc_performance(df_fixed,   "A. 固定仓位(10%)")
        stat_dynamic = calc_performance(df_dynamic, "B. 动态仓位(15%/5%)")

        print(f"\n{'='*70}")
        print("  ┌─────────────────────┬──────────────┬──────────────┐")
        print(f"  │ 指标                │ 固定10%      │ 动态15%/5%  │")
        print("  ├─────────────────────┼──────────────┼──────────────┤")
        print(f"  │ 总收益率            │ {stat_fixed['total_ret']:>10.2%}  │ {stat_dynamic['total_ret']:>10.2%}  │")
        print(f"  │ 最大回撤            │ {stat_fixed['max_dd']:>10.2%}  │ {stat_dynamic['max_dd']:>10.2%}  │")
        print(f"  │ 夏普比率            │ {stat_fixed['sharpe']:>10.3f}  │ {stat_dynamic['sharpe']:>10.3f}  │")
        print(f"  │ 月收益标准差        │ {stat_fixed['monthly_std']:>10.3f}  │ {stat_dynamic['monthly_std']:>10.3f}  │")
        print("  └─────────────────────┴──────────────┴──────────────┘")

        # 建议
        if stat_dynamic["max_dd"] < stat_fixed["max_dd"] or \
           stat_dynamic["monthly_std"] < stat_fixed["monthly_std"]:
            print("\n  ✅ 动态仓位方案回撤更小/收益更平滑，建议更新 trade_plan.py")
        else:
            print("\n  ⚠️ 动态仓位未能显著优于固定仓位，建议保持原策略并优化市场模式判断阈值")

        if args.output:
            combined = pd.concat([
                df_fixed.assign(scheme="fixed"),
                df_dynamic.assign(scheme="dynamic")
            ], ignore_index=True)
            out_path = args.output if os.path.isabs(args.output) else os.path.join(ROOT_DIR, args.output)
            combined.to_csv(out_path, index=False, encoding="utf-8-sig")
            print(f"\n[OUTPUT] 结果已保存到 {out_path}")

    finally:
        conn.close()

    print(f"\n[DONE] 动态仓位回测完成，耗时: {time.time()-t0:.2f} 秒")


if __name__ == "__main__":
    main()

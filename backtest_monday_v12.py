# -*- coding: utf-8 -*-
"""
周波段·周一战法 v1.2 离线量化回测脚本
========================================
核心机制：
  1. 周一盘后筛选：板块强度 + 周线 MACD + 日线 MA20 + 近5日资金 + 量比。
  2. 周二建仓：开盘价买入，仓位受情绪（炸板率/连板率）限制。
  3. 周三至周四跟踪：5% 止损或保本移动止损。
  4. 周五强制清仓：尾盘以收盘价无条件出局。
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

STOP_LOSS_PCT = 0.05  # 周一战法 5% 止损
HOLD_DAYS = 4         # 周二建仓，周五清仓（最多4天）
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
    print(f"[LOAD] 加载周一战法数据，预热起点: {pre_start} ~ 终点: {end_date}")

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
    """
    合成周线并计算周 MACD。
    对每只股票，重采样到每周日，计算 weekly MACD，然后 forward-fill 映射回日线。
    """
    print("[MACD] 向量化计算周 MACD 因子...")
    
    # 确保日期格式
    df = daily_df.copy()
    df["trade_date_dt"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    
    weekly_list = []
    
    # 按股票重采样
    for code, grp in df.groupby("ts_code"):
        grp = grp.sort_values("trade_date_dt")
        # 将 trade_date_dt 设为索引以进行 resample
        grp.set_index("trade_date_dt", inplace=True)
        
        # 周采样：开、高、低、收
        w_df = grp["close"].resample("W").last().to_frame()
        w_df["ts_code"] = code
        w_df = w_df.dropna(subset=["close"])
        
        # 计算周 MACD (DIF, DEA, HIST)
        w_df["ema12"] = w_df["close"].ewm(span=12, adjust=False).mean()
        w_df["ema26"] = w_df["close"].ewm(span=26, adjust=False).mean()
        w_df["dif"] = w_df["ema12"] - w_df["ema26"]
        w_df["dea"] = w_df["dif"].ewm(span=9, adjust=False).mean()
        w_df["macd_hist"] = (w_df["dif"] - w_df["dea"]) * 2
        
        weekly_list.append(w_df.reset_index())

    weekly_all = pd.concat(weekly_list, ignore_index=True)
    
    # 提取回日线。我们将周最后一天的 MACD，赋予下一周所有的交易日。
    # 用 merge_asof 可以极快地对齐
    df = df.sort_values("trade_date_dt")
    weekly_all = weekly_all.sort_values("trade_date_dt")
    
    # merge_asof 要求 trade_date_dt 已经升序
    # 我们要让每周日计算出的周线，赋给该周日之后（也就是下一周）的交易日
    # direction='backward' 表示匹配小于等于日期的最近一个周日 MACD
    df_merged = pd.merge_asof(
        df,
        weekly_all[["trade_date_dt", "ts_code", "dif", "dea", "macd_hist"]],
        on="trade_date_dt",
        by="ts_code",
        direction="backward"
    )
    
    # 计算周线 MACD 信号：红柱放大（今日红柱 > 昨日红柱 且 今日dif > 今日dea）
    # 或者dif金叉dea
    df_merged = df_merged.sort_values(["ts_code", "trade_date"])
    df_merged["prev_macd_hist"] = df_merged.groupby("ts_code")["macd_hist"].shift(1)
    df_merged["weekly_macd_ok"] = (df_merged["macd_hist"] > df_merged["prev_macd_hist"]) & (df_merged["dif"] > df_merged["dea"])

    return df_merged


# =============================================================================
# 周二建仓 & 周五强制平仓组合模拟
# =============================================================================
def run_monday_strategy_simulation(daily_res, money, trade_dates, conn, start_date):
    from market_env import get_market_mode
    
    # 1. 预计算日线 MA20 与量比、筹码上沿
    print("[BACKTEST] 向量化计算日线 MA20 及资金累计因子...")
    df = daily_res.sort_values(["ts_code", "trade_date"]).copy()
    df["ma20"] = df.groupby("ts_code")["close"].transform(lambda x: x.rolling(20).mean())
    df["prev_ma20"] = df.groupby("ts_code")["ma20"].shift(1)
    df["ma20_up"] = df["ma20"] >= df["prev_ma20"]
    df["ma20_ok"] = (df["close"] > df["ma20"]) & df["ma20_up"]

    # 股价到前高（过去60日收盘最高）的距离作为抛压空间
    df["high_60d"] = df.groupby("ts_code")["close"].transform(lambda x: x.rolling(60).max())
    df["space_ok"] = (df["high_60d"] - df["close"]) / df["close"] > 0.03

    # 量比因子（当日vol / 5日vol均值）
    df["vol_5d_mean"] = df.groupby("ts_code")["vol"].transform(lambda x: x.rolling(5).mean())
    df["volume_ratio"] = df["vol"] / df["vol_5d_mean"].replace(0, np.nan)
    df["vol_ratio_ok"] = (df["volume_ratio"] >= 0.8) & (df["volume_ratio"] <= 1.5)

    # 资金流因子合并
    print("[BACKTEST] 向量化计算资金面近5日累加量...")
    mdf = money.sort_values(["ts_code", "trade_date"]).copy()
    mdf["net_main_5d_sum"] = mdf.groupby("ts_code")["net_main"].transform(lambda x: x.rolling(5).sum())
    mdf["inflow_day"] = mdf["net_main"] > 0
    mdf["inflow_5d_cnt"] = mdf.groupby("ts_code")["inflow_day"].transform(lambda x: x.rolling(5).sum())
    mdf["money_ok"] = (mdf["net_main_5d_sum"] > 0) & (mdf["inflow_5d_cnt"] >= 3)

    # 合并回日线
    df = df.merge(mdf[["ts_code", "trade_date", "money_ok"]], on=["ts_code", "trade_date"], how="left")
    df["money_ok"] = df["money_ok"].fillna(False)

    # 曾涨停或近3日有涨停（自动排除连板）
    df["is_limit"] = df["pct_chg"] >= 9.8
    df["limit_3d_sum"] = df.groupby("ts_code")["is_limit"].transform(lambda x: x.rolling(3).sum())

    # 2. 建立日线快速索引
    price_by_code = {}
    for code, grp in df.sort_values("trade_date").groupby("ts_code"):
        price_by_code[code] = grp[["trade_date", "open", "high", "low", "close", "pct_chg"]].reset_index(drop=True)

    # 3. 按周对齐日期
    df_dates = pd.DataFrame({"trade_date": trade_dates})
    df_dates["trade_date_dt"] = pd.to_datetime(df_dates["trade_date"], format="%Y%m%d")
    df_dates["week"] = df_dates["trade_date_dt"].dt.isocalendar().week
    df_dates["year"] = df_dates["trade_date_dt"].dt.isocalendar().year
    
    # 找出每周的交易日明细
    weeks_list = []
    for (yr, wk), grp in df_dates.groupby(["year", "week"]):
        w_days = grp.sort_values("trade_date")["trade_date"].tolist()
        if len(w_days) >= 2: # 至少有2个交易日才能成周
            weeks_list.append({
                "year_week": f"{yr}_{wk}",
                "monday": w_days[0],       # 当周首个交易日
                "tuesday": w_days[1],      # 当周第二个交易日
                "friday": w_days[-1],       # 当周最后一个交易日
                "days": w_days
            })

    # 4. 模拟组合状态
    cash = INITIAL_CAPITAL
    active_positions = {}  # ts_code -> {entry_price, stop_price, shares, current_price, hold_days}
    
    trade_history = []
    portfolio_equity = []
    
    n_stopped = 0
    n_cleared = 0
    
    print(f"[BACKTEST] 运行 2025 全年 {len(weeks_list)} 个周度循环...")
    
    for w_idx, week_info in enumerate(weeks_list):
        mon = week_info["monday"]
        tue = week_info["tuesday"]
        fri = week_info["friday"]
        w_days = week_info["days"]
        
        # 1. 周一盘前：获取情绪炸板/连板率，确定总仓位上限与价格过滤线
        # 我们用 get_market_mode 获取周一状态
        # get_market_mode 会读取缓存的连板、炸板率
        color, max_total_pos, detail_json = get_market_mode(conn=conn, target_date=mon, persist=False)
        single_pos_pct = SINGLE_POS_LIMITS.get(color, 0.05)
        
        # 情绪修正：如果炸板率 > 40% 或 连板率 < 30%，进入谨慎状态
        try:
            detail = json.loads(detail_json)
            explode_rate = detail.get("explode_rate", 0.0)
            promotion_rate = detail.get("promotion_rate", 1.0)
            if explode_rate > 0.40 or promotion_rate < 0.30:
                max_total_pos = 0.30
                single_pos_pct = 0.08
        except Exception:
            pass

        # 2. 周一筛选：寻找目标标的
        mon_df = df[df["trade_date"] == mon].copy()
        if mon_df.empty:
            continue
            
        # 行业前30%强度计算
        # 计算行业近3天平均涨跌幅
        df_3d = df[df["trade_date"] <= mon].sort_values(["ts_code", "trade_date"])
        df_3d_grouped = df_3d.groupby("ts_code").tail(3)
        
        ind_gain = df_3d_grouped.groupby("industry")["pct_chg"].mean().reset_index()
        ind_gain["rank_pct"] = ind_gain["pct_chg"].rank(pct=True)
        strong_industries = ind_gain[ind_gain["rank_pct"] >= 0.70]["industry"].tolist()

        # 因子过滤
        candidates = mon_df[
            mon_df["industry"].isin(strong_industries) &
            (mon_df["weekly_macd_ok"] == True) &
            (mon_df["ma20_ok"] == True) &
            (mon_df["money_ok"] == True) &
            (mon_df["vol_ratio_ok"] == True) &
            (mon_df["space_ok"] == True) &
            (~mon_df["name"].str.contains("ST")) &
            (~mon_df["name"].str.contains("st")) &
            (mon_df["limit_3d_sum"] == 0) # 排除连板/当天涨停
        ].copy()

        # 小账户价格排除限制
        # 对于15万账户，单票1手价格 <= 总资产的15% (即股价 <= 225.0元)
        current_equity = cash + sum(pos["current_price"] * pos["shares"] for pos in active_positions.values())
        max_stock_price = current_equity * 0.15 / 100.0  # 1手是100股
        candidates = candidates[candidates["close"] <= max_stock_price]

        # 挑选 Top-3 评分最高（按照近5日主力流入额之和）
        candidates["score_flow"] = candidates["ts_code"].map(
            df_3d_grouped.groupby("ts_code")["vol"].sum()
        )
        top_candidates = candidates.sort_values("score_flow", ascending=False).head(3)["ts_code"].tolist()

        # 3. 周二建仓（全周仅在周二开盘买入）
        # 开盘前先检查当前是否有持仓，由于周五强制清仓，此处 active_positions 必然为空
        active_positions.clear()
        
        # 黑色/极端风险下空头暂停建仓
        if color != "black" and max_total_pos > 0.0:
            for code in top_candidates:
                df_p = price_by_code.get(code)
                if df_p is None:
                    continue
                tue_row = df_p[df_p["trade_date"] == tue]
                if tue_row.empty:
                    continue
                
                open_price = float(tue_row.iloc[0]["open"])
                if open_price <= 0:
                    continue
                
                # 限仓：单票建仓比例受总仓上限和单票仓位控制
                # 小账户小仓位对冲流动性风险：单票建仓上限 12%
                final_single_pct = min(single_pos_pct, 0.12)
                
                buy_value = current_equity * final_single_pct
                shares = int(buy_value / open_price)
                
                # 扣款
                if shares > 0 and cash >= shares * open_price:
                    cash -= shares * open_price
                    
                    # 止损价设定：买入价-5% 或者是 周二当天MA20均线价的较高者
                    tue_ma20 = float(df[(df["ts_code"] == code) & (df["trade_date"] == tue)]["ma20"].iloc[0])
                    stop_price = max(open_price * 0.95, tue_ma20)
                    
                    active_positions[code] = {
                        "entry_date": tue,
                        "entry_price": open_price,
                        "stop_price": stop_price,
                        "shares": shares,
                        "current_price": open_price,
                        "hold_days": 0,
                        "breakeven_triggered": False  # 保本止损标记
                    }

        # 4. 周三至周五逐日跟踪
        # 周三、周四、周五天数遍历
        action_days = w_days[2:] if len(w_days) >= 3 else []
        
        for d in w_days:
            # 记录每日总资产
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
            
            # 如果是周二以后，我们要日常跟踪止损
            if d != tue and d != mon:
                closed_codes = []
                for code, pos in active_positions.items():
                    df_p = price_by_code.get(code)
                    today_row = df_p[df_p["trade_date"] == d] if df_p is not None else None
                    if today_row is None or today_row.empty:
                        continue
                    
                    day_low = float(today_row.iloc[0]["low"])
                    day_close = float(today_row.iloc[0]["close"])
                    day_high = float(today_row.iloc[0]["high"])

                    # 检查是否触发保本止损（最高点涨幅 > 5% 后，止损线上推至买入价）
                    high_gain = (day_high - pos["entry_price"]) / pos["entry_price"]
                    if high_gain >= 0.05 and not pos["breakeven_triggered"]:
                        pos["stop_price"] = pos["entry_price"]
                        pos["breakeven_triggered"] = True

                    # 止损检测
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

                    # 周五强制清仓
                    if d == fri:
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
                            "reason": "⏳ 周五强平清仓"
                        })
                        closed_codes.append(code)
                        n_cleared += 1

                for cc in closed_codes:
                    if cc in active_positions:
                        del active_positions[cc]

        # 确保到周五结束时，active_positions 完全被清空
        active_positions.clear()

    df_equity = pd.DataFrame(portfolio_equity)
    df_trades = pd.DataFrame(trade_history)

    print(f"  回测完成。期末资产: ¥{cash:,.2f} | 止损平仓: {n_stopped} 次 | 周五强平: {n_cleared} 次")
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
    print("      StockAI v4.0 - 周波段·周一战法 v1.2 量化研究报告")
    print(f"{'='*70}")
    print(f"\n【周战法交易绩效】")
    print(f"  交易总笔数  : {total_trades:,} 笔")
    print(f"  信号平均胜率: {win_rate:.1%}  {'✅' if win_rate >= 0.60 else '⚠️'}")
    print(f"  单笔均收益  : {avg_ret:+.2f}%")
    print(f"  平均盈利    : {gains:+.2f}%")
    print(f"  平均亏损    : {losses:+.2f}%")
    print(f"  系统盈亏比  : {pl_ratio:.2f}")
    print(f"  最大单笔亏损: {max_loss:+.2f}% (受5%与MA20动态风控保护)")

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
    parser = argparse.ArgumentParser(description="周波段·周一战法 v1.2 离线回测")
    parser.add_argument("--start",  default="20250601")
    parser.add_argument("--end",    default="20251231")
    parser.add_argument("--output", default="reports/backtest_monday_v12.csv")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")

    try:
        daily, money = load_all_data(conn, args.start, args.end)
        
        if daily.empty:
            print("[ERROR] 本地日线数据为空，终止回测")
            return
            
        # 合成周 MACD
        daily_res = compute_weekly_macd(daily)
        
        trade_dates = sorted(
            daily[daily["trade_date"] >= args.start]["trade_date"].unique().tolist()
        )
        
        # 运行周战法模拟
        df_equity, df_trades = run_monday_strategy_simulation(daily_res, money, trade_dates, conn, args.start)
        
        # 绩效分析
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

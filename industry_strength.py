# -*- coding: utf-8 -*-
"""
industry_strength.py —— StockAI v4.0 行业强度锁定模块
=====================================================================
每日盘后运行一次，计算申万行业（按 stock_list.industry 字段聚合）强度。

计算方法（无需额外 Tushare 接口权限）：
  1. 从 daily_prices 取全市场最近 5 个交易日行情
  2. 从 moneyflow 取近 5 日主力资金数据
  3. 按 stock_list.industry 分组，计算：
       - avg_pct_chg    ：行业近5日平均涨跌幅（等权）
       - net_mf_amount  ：行业近5日主力净流入总额（亿元）
       - stock_count    ：覆盖股票数量
  4. 综合得分 = avg_pct_chg 标准化(60%) + net_mf_amount 标准化(40%)
  5. 涨幅前5且净流入>0 → 主线行业
     第6-10名          → 备选行业
     其余              → 回避

结果写入：
  - SQLite 表 industry_rank（每次 INSERT OR REPLACE）
  - industry_rank.json（供快速读取，无需查DB）
"""

import os
import json
import logging
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime
from typing import List, Tuple, Dict

ROOT_DIR     = os.path.dirname(os.path.abspath(__file__))
DB_PATH      = os.path.join(ROOT_DIR, "db", "stock_daily.db")
RANK_JSON    = os.path.join(ROOT_DIR, "industry_rank.json")

log = logging.getLogger(__name__)

# 主线行业入选阈值
MAIN_TOP_N   = 5   # 综合得分前5为主线
BACKUP_TOP_N = 10  # 第6-10名为备选


# =============================================================================
# 数据库初始化
# =============================================================================

def _init_industry_rank_table(conn: sqlite3.Connection) -> None:
    """创建 industry_rank 表（若不存在）。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_rank (
            calc_date      TEXT NOT NULL,
            industry       TEXT NOT NULL,
            avg_pct_chg    REAL,
            net_mf_amount  REAL,
            stock_count    INTEGER,
            composite_score REAL,
            tier           TEXT,
            PRIMARY KEY (calc_date, industry)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ir_date ON industry_rank (calc_date)"
    )
    conn.commit()


# =============================================================================
# 核心计算
# =============================================================================

def _get_recent_trade_dates(conn: sqlite3.Connection, n: int = 5,
                            target_date: str = None) -> List[str]:
    """
    取最近 n 个有效交易日日期。
    target_date: 若传入，只取 <= target_date 的日期（履测历史截断）。
    """
    if target_date:
        rows = conn.execute(
            """SELECT DISTINCT trade_date FROM daily_prices
               WHERE trade_date <= ?
               ORDER BY trade_date DESC LIMIT ?""",
            (target_date, n)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT DISTINCT trade_date FROM daily_prices
               ORDER BY trade_date DESC LIMIT ?""",
            (n,)
        ).fetchall()
    return [r[0] for r in rows]


def _load_price_data(conn: sqlite3.Connection,
                     trade_dates: List[str]) -> pd.DataFrame:
    """加载近几日全市场日线数据（仅取需要的字段）。"""
    placeholders = ",".join("?" * len(trade_dates))
    return pd.read_sql(
        f"""SELECT dp.ts_code, dp.trade_date, dp.pct_chg
            FROM daily_prices dp
            WHERE dp.trade_date IN ({placeholders})
              AND dp.pct_chg IS NOT NULL""",
        conn, params=trade_dates
    )


def _load_moneyflow_data(conn: sqlite3.Connection,
                         trade_dates: List[str]) -> pd.DataFrame:
    """加载近几日全市场资金流向。"""
    placeholders = ",".join("?" * len(trade_dates))
    try:
        return pd.read_sql(
            f"""SELECT ts_code, trade_date,
                       buy_elg_amount, sell_elg_amount,
                       buy_lg_amount,  sell_lg_amount,
                       net_mf_amount
                FROM moneyflow
                WHERE trade_date IN ({placeholders})""",
            conn, params=trade_dates
        )
    except Exception as e:
        log.warning("资金流向数据读取失败: %s", e)
        return pd.DataFrame()


def _load_stock_industry(conn: sqlite3.Connection) -> pd.DataFrame:
    """加载股票-行业映射表。"""
    return pd.read_sql(
        """SELECT ts_code, industry FROM stock_list
           WHERE industry IS NOT NULL AND industry != ''""",
        conn
    )


def _normalize_series(s: pd.Series) -> pd.Series:
    """Min-Max 标准化到 [0, 1]，若全为相同值返回0.5。"""
    mn, mx = s.min(), s.max()
    if mx == mn:
        return pd.Series([0.5] * len(s), index=s.index)
    return (s - mn) / (mx - mn)


def calc_industry_strength(conn: sqlite3.Connection,
                           target_date: str = None) -> pd.DataFrame:
    """
    计算各行业综合强度得分。

    参数：
        conn        SQLite 连接
        target_date 如果传入（YYYYMMDD），只取 <= target_date 的近 5 日数据
                    （回测模式：严禁使用未来数据）。不传则取全库最新。

    返回 DataFrame，列：
        industry, avg_pct_chg, net_mf_amount, stock_count,
        composite_score, tier
    """
    trade_dates = _get_recent_trade_dates(conn, n=5, target_date=target_date)
    if not trade_dates:
        log.warning("calc_industry_strength: 无有效交易日数据 (target_date=%s)", target_date)
        return pd.DataFrame()

    log.debug("calc_industry_strength | target=%s | 日期: %s ~ %s",
              target_date or 'latest', trade_dates[-1], trade_dates[0])

    # 加载数据
    df_price   = _load_price_data(conn, trade_dates)
    df_money   = _load_moneyflow_data(conn, trade_dates)
    df_ind     = _load_stock_industry(conn)

    if df_price.empty or df_ind.empty:
        log.warning("价格或行业数据为空，无法计算行业强度")
        return pd.DataFrame()

    # ── 涨跌幅按行业聚合 ────────────────────────────────────────────────────
    df_price_ind = df_price.merge(df_ind, on="ts_code", how="left")
    df_price_ind = df_price_ind.dropna(subset=["industry"])

    price_group = df_price_ind.groupby("industry").agg(
        avg_pct_chg  = ("pct_chg", "mean"),
        stock_count  = ("ts_code", "nunique"),
    ).reset_index()

    # 过滤覆盖股票数 < 3 的小众行业（避免噪声）
    price_group = price_group[price_group["stock_count"] >= 3]

    # ── 资金流向按行业聚合 ──────────────────────────────────────────────────
    if not df_money.empty:
        # 计算主力净流入（大单 + 特大单）
        for col in ["buy_elg_amount", "sell_elg_amount",
                    "buy_lg_amount",  "sell_lg_amount"]:
            if col not in df_money.columns:
                df_money[col] = 0.0
        df_money["net_main"] = (
            (df_money["buy_elg_amount"].fillna(0) + df_money["buy_lg_amount"].fillna(0))
            - (df_money["sell_elg_amount"].fillna(0) + df_money["sell_lg_amount"].fillna(0))
        )
        # 若有 net_mf_amount 字段，优先用全市场资金（更准确）
        if "net_mf_amount" in df_money.columns:
            df_money["net_main"] = df_money["net_mf_amount"].fillna(df_money["net_main"])

        df_money_ind = df_money.merge(df_ind, on="ts_code", how="left")
        df_money_ind = df_money_ind.dropna(subset=["industry"])

        money_group = df_money_ind.groupby("industry").agg(
            net_mf_amount = ("net_main", "sum")
        ).reset_index()
        # 转换单位：万元 → 亿元
        money_group["net_mf_amount"] = money_group["net_mf_amount"] / 10000
    else:
        money_group = pd.DataFrame(columns=["industry", "net_mf_amount"])

    # ── 合并 & 计算综合得分 ──────────────────────────────────────────────────
    df_result = price_group.merge(money_group, on="industry", how="left")
    df_result["net_mf_amount"] = df_result["net_mf_amount"].fillna(0.0)

    # Min-Max 标准化 → 综合得分（涨幅权重60%，资金权重40%）
    df_result["score_pct"] = _normalize_series(df_result["avg_pct_chg"])
    df_result["score_mf"]  = _normalize_series(df_result["net_mf_amount"])
    df_result["composite_score"] = (
        df_result["score_pct"] * 0.60 + df_result["score_mf"] * 0.40
    )

    # 排序
    df_result = df_result.sort_values("composite_score", ascending=False).reset_index(drop=True)
    df_result["rank"] = df_result.index + 1

    # ── 分层：主线 / 备选 / 回避 ────────────────────────────────────────────
    def _assign_tier(row) -> str:
        if row["rank"] <= MAIN_TOP_N and row["net_mf_amount"] >= 0:
            return "main"       # 主线
        elif row["rank"] <= BACKUP_TOP_N:
            return "backup"     # 备选
        else:
            return "avoid"      # 回避

    df_result["tier"] = df_result.apply(_assign_tier, axis=1)

    # 清理中间列
    df_result = df_result.drop(columns=["score_pct", "score_mf", "rank"])

    log.info("行业强度计算完成：共 %d 个行业 | 主线 %d | 备选 %d",
             len(df_result),
             (df_result["tier"] == "main").sum(),
             (df_result["tier"] == "backup").sum())

    return df_result


# =============================================================================
# 持久化
# =============================================================================

def _save_to_db(conn: sqlite3.Connection,
                df: pd.DataFrame, calc_date: str) -> None:
    """将行业强度结果写入 industry_rank 表。"""
    _init_industry_rank_table(conn)
    rows = [
        (calc_date, row["industry"], row["avg_pct_chg"],
         row["net_mf_amount"], int(row["stock_count"]),
         row["composite_score"], row["tier"])
        for _, row in df.iterrows()
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO industry_rank
           (calc_date, industry, avg_pct_chg, net_mf_amount,
            stock_count, composite_score, tier)
           VALUES (?,?,?,?,?,?,?)""",
        rows
    )
    conn.commit()
    log.info("industry_rank 表已更新：%d 行（日期 %s）", len(rows), calc_date)


def _save_to_json(df: pd.DataFrame, calc_date: str) -> None:
    """将行业强度结果写入 industry_rank.json。"""
    main_list   = df[df["tier"] == "main"]["industry"].tolist()
    backup_list = df[df["tier"] == "backup"]["industry"].tolist()

    # 完整排行榜（前20名）
    top20 = []
    for _, row in df.head(20).iterrows():
        top20.append({
            "industry":       row["industry"],
            "avg_pct_chg":    round(float(row["avg_pct_chg"]), 3),
            "net_mf_amount":  round(float(row["net_mf_amount"]), 2),
            "stock_count":    int(row["stock_count"]),
            "composite_score": round(float(row["composite_score"]), 4),
            "tier":           row["tier"],
        })

    payload = {
        "calc_date":   calc_date,
        "timestamp":   datetime.now().isoformat(),
        "main_line":   main_list,
        "backup_line": backup_list,
        "all_industries": top20,
    }

    with open(RANK_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log.info("industry_rank.json 已更新 → 主线: %s", main_list)


# =============================================================================
# 主接口
# =============================================================================

def get_strong_industries(conn: sqlite3.Connection = None,
                          db_path: str = DB_PATH,
                          target_date: str = None,
                          persist: bool = True) -> Tuple[List[str], List[str]]:
    """
    计算并返回当前强势行业列表（同时持久化结果）。

    参数：
        conn        已有的 SQLite 连接（可选）
        db_path     数据库路径
        target_date 如果传入（YYYYMMDD），只取到该日期为止的行业强度
                    （回测模式：严禁未来数据）
        persist     是否将结果写入 DB/JSON（回测时设 False 避免污染实盘数据）

    返回：
        (main_line: List[str], backup_line: List[str])
    """
    _own_conn = conn is None
    if _own_conn:
        conn = sqlite3.connect(db_path, check_same_thread=False)

    try:
        df = calc_industry_strength(conn, target_date=target_date)
        if df.empty:
            return [], []

        calc_date = target_date or datetime.now().strftime("%Y%m%d")
        if persist:
            _save_to_db(conn, df, calc_date)
            _save_to_json(df, calc_date)

        main_list   = df[df["tier"] == "main"]["industry"].tolist()
        backup_list = df[df["tier"] == "backup"]["industry"].tolist()
        return main_list, backup_list

    finally:
        if _own_conn:
            conn.close()


def load_strong_industries() -> Tuple[List[str], List[str]]:
    """
    从 industry_rank.json 读取缓存的行业列表（供快速调用，不查DB）。
    若文件不存在或过期（>24小时），返回空列表（不过滤行业）。

    返回：
        (main_line: List[str], backup_line: List[str])
    """
    if not os.path.exists(RANK_JSON):
        log.warning("industry_rank.json 不存在，行业过滤不启用")
        return [], []

    try:
        with open(RANK_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)

        ts_str = data.get("timestamp", "")
        if ts_str:
            ts = datetime.fromisoformat(ts_str)
            age_hours = (datetime.now() - ts).total_seconds() / 3600
            if age_hours > 24:
                log.warning("industry_rank.json 已过期（%.1f 小时）", age_hours)

        return data.get("main_line", []), data.get("backup_line", [])

    except Exception as e:
        log.warning("读取 industry_rank.json 失败: %s", e)
        return [], []


# =============================================================================
# CLI 独立测试入口
# =============================================================================
if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    print("=" * 60)
    print("  StockAI v4.0 - Industry Strength Module Test")
    print("=" * 60)

    main_line, backup_line = get_strong_industries()

    print(f"\n  [MAIN] Top{MAIN_TOP_N} Industries:")
    for i, ind in enumerate(main_line, 1):
        print(f"     {i}. {ind}")

    print(f"\n  [BACKUP] Rank {MAIN_TOP_N+1}-{BACKUP_TOP_N} Industries:")
    for i, ind in enumerate(backup_line, 1):
        print(f"     {MAIN_TOP_N+i}. {ind}")

    # 加载详细榜单
    if os.path.exists(RANK_JSON):
        with open(RANK_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"\n  {'Rank':<4} {'Industry':<15} {'5d Avg Chg':<12} {'Net MF(100M)':<14} {'Tier'}")
        print("  " + "-" * 55)
        for i, row in enumerate(data.get("all_industries", [])[:15], 1):
            tier_zh = {"main": "[*] MAIN", "backup": "[+] BACKUP", "avoid": "-"}.get(row["tier"], "")
            print(f"  {i:<4} {row['industry']:<15} "
                  f"{row['avg_pct_chg']:>+9.2f}%   "
                  f"{row['net_mf_amount']:>12.1f}   {tier_zh}")

    print(f"\n  Result saved -> {RANK_JSON}")

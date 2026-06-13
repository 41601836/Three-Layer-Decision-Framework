# -*- coding: utf-8 -*-
"""
industry_strength.py —— StockAI v4.0 行业强度与热度综合计算模块 (A股优化版)
=====================================================================
每日盘后或按需运行，计算行业（按 stock_list.industry 字段聚合）的热度与资金持续性。

计算指标：
  1. 资金强度（money_flow_intensity）：板块主力净流入额（亿元，由 moneyflow.net_mf_amount 聚合）。
  2. 资金持续性（money_flow_dur）：N个交易日内，板块整体主力资金呈净流入的天数。
  3. 成交占比（volume_ratio）：板块成交额占全市场的比例（%，由 daily_prices.amount 聚合）。
  4. 上涨家数占比（rise_ratio）：板块内日均上涨股票数占比（%）。

归一化评分算法（0.0 ~ 1.0）：
  综合评分 = 资金强度(30%) + 资金持续性(30%) + 成交占比(20%) + 上涨家数占比(20%)
  按评分排序分层：前5名且资金强度>=0为 [主线行业]；第6-10名为 [备选行业]；其余为 [回避行业]。

结果写入：
  - SQLite 表 industry_rank（每次 INSERT OR REPLACE 近5日主线排行）
  - industry_rank.json（缓存日、周、月三个维度的热度数据）
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

# 主线/备选行业数量划分
MAIN_TOP_N   = 5
BACKUP_TOP_N = 10


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
# 核心数据加载
# =============================================================================

def _get_recent_trade_dates(conn: sqlite3.Connection, n: int = 5,
                            target_date: str = None) -> List[str]:
    """获取最近 n 个有效交易日日期。"""
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
    """加载近几日全市场日线行情数据（包含成交额）。"""
    placeholders = ",".join("?" * len(trade_dates))
    return pd.read_sql(
        f"""SELECT ts_code, trade_date, pct_chg, amount
            FROM daily_prices
            WHERE trade_date IN ({placeholders})
              AND pct_chg IS NOT NULL""",
        conn, params=trade_dates
    )


def _load_moneyflow_data(conn: sqlite3.Connection,
                         trade_dates: List[str]) -> pd.DataFrame:
    """加载近几日全市场资金流向。"""
    placeholders = ",".join("?" * len(trade_dates))
    try:
        return pd.read_sql(
            f"""SELECT ts_code, trade_date, net_mf_amount
                FROM moneyflow
                WHERE trade_date IN ({placeholders})""",
            conn, params=trade_dates
        )
    except Exception as e:
        log.warning("资金流向数据读取失败: %s", e)
        return pd.DataFrame()


def _load_stock_industry(conn: sqlite3.Connection) -> pd.DataFrame:
    """加载股票-行业映射。"""
    return pd.read_sql(
        """SELECT ts_code, industry FROM stock_list
           WHERE industry IS NOT NULL AND industry != ''""",
        conn
    )


def _normalize_series(s: pd.Series) -> pd.Series:
    """Min-Max 标准化到 [0, 1]，若全为相同值返回 0.5。"""
    mn, mx = s.min(), s.max()
    if mx == mn:
        return pd.Series([0.5] * len(s), index=s.index)
    return (s - mn) / (mx - mn)


# =============================================================================
# 热度计算引擎
# =============================================================================

def calc_industry_strength_for_period(conn: sqlite3.Connection, n_days: int = 5,
                                      target_date: str = None, block_type: str = "industry") -> pd.DataFrame:
    """
    计算特定周期下的行业/板块综合热度与资金持续性。
    支持按行业 (industry) 统计，具备极佳的扩展性。
    """
    trade_dates = _get_recent_trade_dates(conn, n=n_days, target_date=target_date)
    if not trade_dates:
        log.warning("calc_industry_strength_for_period: 无有效交易日数据 (n_days=%d)", n_days)
        return pd.DataFrame()

    df_price = _load_price_data(conn, trade_dates)
    df_money = _load_moneyflow_data(conn, trade_dates)
    df_ind   = _load_stock_industry(conn)

    if df_price.empty or df_ind.empty:
        log.warning("价格行情或板块映射为空，无法计算热度")
        return pd.DataFrame()

    # 聚合核心关系
    df_p = df_price.merge(df_ind, on="ts_code", how="inner")

    # 1. 资金强度与持续性计算
    if not df_money.empty:
        df_m = df_money.merge(df_ind, on="ts_code", how="inner")
        # 1.1 资金强度：个股主力净流入和。单位万元 -> 亿元
        money_intensity = df_m.groupby("industry")["net_mf_amount"].sum().reset_index()
        money_intensity["net_mf_amount"] = money_intensity["net_mf_amount"].fillna(0.0) / 10000.0

        # 1.2 资金持续性：统计N天中该行业每日整体资金流入为正的天数
        daily_mf = df_m.groupby(["industry", "trade_date"])["net_mf_amount"].sum().reset_index()
        daily_mf["is_positive"] = daily_mf["net_mf_amount"] > 0
        money_dur = daily_mf.groupby("industry")["is_positive"].sum().reset_index()
        money_dur.rename(columns={"is_positive": "money_flow_dur"}, inplace=True)
    else:
        money_intensity = pd.DataFrame(columns=["industry", "net_mf_amount"])
        money_dur = pd.DataFrame(columns=["industry", "money_flow_dur"])

    # 2. 成交额及占比计算
    market_total_amount = df_p["amount"].sum()
    if market_total_amount <= 0:
        market_total_amount = 1.0

    ind_amount = df_p.groupby("industry")["amount"].sum().reset_index()
    # 2.1 成交额占比（百分比）
    ind_amount["volume_ratio"] = (ind_amount["amount"] / market_total_amount) * 100.0
    # 2.2 板块日均成交额（单位为“亿元”，amount单位为千元）
    ind_amount["avg_amount_yi"] = (ind_amount["amount"] / len(trade_dates)) / 100000.0

    # 3. 日均上涨家数占比
    df_p["is_rise"] = df_p["pct_chg"] > 0
    df_p["is_trade"] = df_p["pct_chg"].notna()
    
    daily_rise = df_p.groupby(["industry", "trade_date"]).agg(
        rise_cnt = ("is_rise", "sum"),
        trade_cnt = ("is_trade", "sum")
    ).reset_index()
    
    daily_rise["rise_ratio"] = daily_rise["rise_cnt"] / daily_rise["trade_cnt"].replace(0, 1)
    avg_rise = daily_rise.groupby("industry")["rise_ratio"].mean().reset_index()
    # 换算为百分比
    avg_rise["rise_ratio"] = avg_rise["rise_ratio"] * 100.0

    # 覆盖个股数与均值涨幅
    stock_count = df_p.groupby("industry")["ts_code"].nunique().reset_index()
    stock_count.rename(columns={"ts_code": "stock_count"}, inplace=True)

    avg_pct = df_p.groupby("industry")["pct_chg"].mean().reset_index()
    avg_pct.rename(columns={"pct_chg": "avg_pct_chg"}, inplace=True)

    # 4. 合并所有维度
    df_res = stock_count.merge(avg_pct, on="industry", how="left")
    df_res = df_res.merge(money_intensity, on="industry", how="left")
    df_res = df_res.merge(money_dur, on="industry", how="left")
    df_res = df_res.merge(ind_amount, on="industry", how="left")
    df_res = df_res.merge(avg_rise, on="industry", how="left")

    df_res["net_mf_amount"]  = df_res["net_mf_amount"].fillna(0.0)
    df_res["money_flow_dur"] = df_res["money_flow_dur"].fillna(0)
    df_res["volume_ratio"]   = df_res["volume_ratio"].fillna(0.0)
    df_res["avg_amount_yi"]  = df_res["avg_amount_yi"].fillna(0.0)
    df_res["rise_ratio"]     = df_res["rise_ratio"].fillna(0.0)

    # 过滤股票数过少的小行业，确保统计稳健
    df_res = df_res[df_res["stock_count"] >= 3]
    if df_res.empty:
        return pd.DataFrame()

    # 5. 归一化评分 (评分分配：资金强度30%，资金持续性30%，成交占比20%，上涨占比20%)
    s_mf   = _normalize_series(df_res["net_mf_amount"])
    s_dur  = _normalize_series(df_res["money_flow_dur"].astype(float))
    s_vol  = _normalize_series(df_res["volume_ratio"])
    s_rise = _normalize_series(df_res["rise_ratio"])

    df_res["composite_score"] = s_mf * 0.3 + s_dur * 0.3 + s_vol * 0.2 + s_rise * 0.2

    # 排序
    df_res = df_res.sort_values("composite_score", ascending=False).reset_index(drop=True)
    df_res["rank"] = df_res.index + 1

    # 6. 分层标记
    def _assign_tier(row) -> str:
        if row["rank"] <= MAIN_TOP_N and row["net_mf_amount"] >= 0:
            return "main"
        elif row["rank"] <= BACKUP_TOP_N:
            return "backup"
        else:
            return "avoid"

    df_res["tier"] = df_res.apply(_assign_tier, axis=1)
    return df_res


def calc_industry_strength(conn: sqlite3.Connection,
                           target_date: str = None) -> pd.DataFrame:
    """维持旧接口兼容：默认计算 5 日周期行业强度。"""
    return calc_industry_strength_for_period(conn, n_days=5, target_date=target_date)


# =============================================================================
# 持久化与缓存机制
# =============================================================================

def _save_to_db(conn: sqlite3.Connection,
                df: pd.DataFrame, calc_date: str) -> None:
    """将近5日行业主线排行保存到 SQLite。"""
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


def _format_list_for_json(df: pd.DataFrame) -> list:
    """格式化 DataFrame 为前端标准 JSON 格式。"""
    if df.empty:
        return []
    result = []
    for _, row in df.head(20).iterrows():
        result.append({
            "name":            row["industry"],
            "change":          round(float(row["avg_pct_chg"]), 2),
            "volume":          round(float(row["avg_amount_yi"]), 2),
            "score":           int(round(row["composite_score"] * 100)),
            "stock_count":     int(row["stock_count"]),
            "tier":            row["tier"],
            "net_mf_amount":   round(float(row["net_mf_amount"]), 2),
            "money_flow_dur":  int(row["money_flow_dur"]),
            "volume_ratio":    round(float(row["volume_ratio"]), 2),
            "rise_ratio":      round(float(row["rise_ratio"]), 2)
        })
    return result


def _save_to_json(df_day: pd.DataFrame, df_week: pd.DataFrame, df_month: pd.DataFrame, calc_date: str) -> None:
    """缓存三时区排行到 industry_rank.json。"""
    day_list   = _format_list_for_json(df_day)
    week_list  = _format_list_for_json(df_week)
    month_list = _format_list_for_json(df_month)

    # 主线与备选线沿用周级别（5日）大底子以向下兼容
    main_list   = df_week[df_week["tier"] == "main"]["industry"].tolist()
    backup_list = df_week[df_week["tier"] == "backup"]["industry"].tolist()

    payload = {
        "calc_date":      calc_date,
        "timestamp":      datetime.now().isoformat(),
        "main_line":      main_list,
        "backup_line":    backup_list,
        "day":            day_list,
        "week":           week_list,
        "month":          month_list
    }

    with open(RANK_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log.info("industry_rank.json 缓存已写入（包含日、周、月排行）")


# =============================================================================
# 主调接口
# =============================================================================

def get_strong_industries(conn: sqlite3.Connection = None,
                           db_path: str = DB_PATH,
                           target_date: str = None,
                           persist: bool = True) -> Tuple[List[str], List[str]]:
    """
    计算并获取日、周、月级强势板块排行（同时持久化）。
    """
    _own_conn = conn is None
    if _own_conn:
        conn = sqlite3.connect(db_path, check_same_thread=False)

    try:
        # 分别计算 1日、5日、20日 三类排行数据
        df_day   = calc_industry_strength_for_period(conn, n_days=1, target_date=target_date)
        df_week  = calc_industry_strength_for_period(conn, n_days=5, target_date=target_date)
        df_month = calc_industry_strength_for_period(conn, n_days=20, target_date=target_date)

        if df_week.empty:
            return [], []

        calc_date = target_date or datetime.now().strftime("%Y%m%d")
        if persist:
            _save_to_db(conn, df_week, calc_date)
            _save_to_json(df_day, df_week, df_month, calc_date)

        main_list   = df_week[df_week["tier"] == "main"]["industry"].tolist()
        backup_list = df_week[df_week["tier"] == "backup"]["industry"].tolist()
        return main_list, backup_list

    finally:
        if _own_conn:
            conn.close()


def load_strong_industries() -> Tuple[List[str], List[str]]:
    """从 industry_rank.json 加载缓存的主线/备选行业。"""
    if not os.path.exists(RANK_JSON):
        log.warning("industry_rank.json 不存在")
        return [], []

    try:
        with open(RANK_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("main_line", []), data.get("backup_line", [])
    except Exception as e:
        log.warning("读取 industry_rank.json 失败: %s", e)
        return [], []


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("=" * 60)
    print("  StockAI v4.0 - Industry Heat & Dur Engine Test")
    print("=" * 60)

    main_line, backup_line = get_strong_industries()
    print(f"\n  [MAIN-LINE] (5d): {main_line}")
    print(f"  [BACKUP-LINE] (5d): {backup_line}")

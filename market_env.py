# -*- coding: utf-8 -*-
"""
market_env.py —— StockAI v4.0 大盘环境判断模块
=====================================================================
每日盘前运行，输出当前市场模式与总仓位上限：
    attack   (进攻)  →  max_pos = 0.80  全仓可用 80%
    defense  (防守)  →  max_pos = 0.30  控制仓位 30%
    empty    (空仓)  →  max_pos = 0.00  今日休战，终止后续流程

判断逻辑（双重降级）：
    一级：查询 daily_prices 中的上证指数 (000001.SH)
          计算60日均线及斜率：
            close > MA60 且 slope > +0.2%  → attack
            close < MA60 且 slope < -0.2%  → empty
            其余                           → defense
    二级（降级）：若无指数数据，改用全市场当日：
          上涨家数占比 + 近5日成交额均值变化
            上涨占比 > 60% 且 近5日均量 > 近20日均量  → attack
            上涨占比 < 40% 且 量能萎缩                → empty
            其余                                       → defense

结果同时写入 market_env.json，供其他模块读取。
"""

import os
import json
import logging
import sqlite3
import pandas as pd
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")
ENV_JSON = os.path.join(ROOT_DIR, "market_env.json")

log = logging.getLogger(__name__)

# 大盘模式对应的最大仓位上限
MAX_POS_MAP = {
    "attack":  0.80,
    "defense": 0.30,
    "empty":   0.00,
}

# 大盘模式中文名
MODE_ZH = {
    "attack":  "进攻",
    "defense": "防守",
    "empty":   "空仓",
}


# =============================================================================
# 一级：上证指数法
# =============================================================================

def _mode_from_index(conn: sqlite3.Connection):
    """
    从 daily_prices 中读取上证指数 (000001.SH) 近120日收盘价。
    返回 (mode, max_pos, detail_str) 或 None（无数据时）。
    """
    try:
        df = pd.read_sql(
            """SELECT trade_date, close
               FROM   daily_prices
               WHERE  ts_code = '000001.SH'
               ORDER  BY trade_date DESC LIMIT 120""",
            conn
        )
    except Exception as e:
        log.debug("查询上证指数失败: %s", e)
        return None

    if df is None or len(df) < 62:
        log.info("daily_prices 中上证指数数据不足（%d 行），启用降级方案", len(df) if df is not None else 0)
        return None

    # 时序从旧到新，计算60日均线
    df = df.iloc[::-1].reset_index(drop=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["ma60"]  = df["close"].rolling(60).mean()

    latest    = df.iloc[-1]
    prev      = df.iloc[-2]
    close_now = latest["close"]
    ma60_now  = latest["ma60"]
    ma60_prev = prev["ma60"]

    if pd.isna(ma60_now) or pd.isna(ma60_prev) or ma60_prev == 0:
        return None

    slope = (ma60_now - ma60_prev) / ma60_prev  # 均线斜率（日变化率）

    detail = (f"上证 {close_now:.2f} | MA60 {ma60_now:.2f} | "
              f"斜率 {slope*100:+.3f}%/日")

    if close_now > ma60_now and slope > 0.002:
        return "attack", MAX_POS_MAP["attack"], detail
    elif close_now < ma60_now and slope < -0.002:
        return "empty", MAX_POS_MAP["empty"], detail
    else:
        return "defense", MAX_POS_MAP["defense"], detail


# =============================================================================
# 二级降级：全市场广度法
# =============================================================================

def _mode_from_market_breadth(conn: sqlite3.Connection,
                              target_date: str = None):
    """
    当指数数据不可用时，通过全市场涨跌情绪判断大盘模式。
    target_date: 若传入，所有查询截止到该日（回测加锁）。
    """
    try:
        # 取指定日期或最新交易日
        if target_date:
            trade_date = conn.execute(
                "SELECT MAX(trade_date) FROM daily_prices WHERE trade_date <= ?",
                (target_date,)
            ).fetchone()[0]
        else:
            trade_date = conn.execute(
                "SELECT MAX(trade_date) FROM daily_prices"
            ).fetchone()[0]

        if not trade_date:
            return "defense", MAX_POS_MAP["defense"], "无数据，默认防守"

        # 全市场当日涨跌统计
        df_today = pd.read_sql(
            """SELECT pct_chg, amount FROM daily_prices
               WHERE trade_date = ? AND pct_chg IS NOT NULL""",
            conn, params=(trade_date,)
        )

        if df_today.empty:
            return "defense", MAX_POS_MAP["defense"], "当日数据为空，默认防守"

        total_stocks = len(df_today)
        up_count     = (df_today["pct_chg"] > 0).sum()
        up_ratio     = up_count / total_stocks if total_stocks > 0 else 0.5

        # 近20日成交额（严格截止 trade_date，不用未来数据）
        df_vol = pd.read_sql(
            """SELECT trade_date, SUM(amount) AS total_amount
               FROM daily_prices
               WHERE trade_date <= ?
               GROUP BY trade_date
               ORDER BY trade_date DESC LIMIT 20""",
            conn, params=(trade_date,)
        )
        vol_5  = df_vol.head(5)["total_amount"].mean()  if len(df_vol) >= 5  else 0
        vol_20 = df_vol.head(20)["total_amount"].mean() if len(df_vol) >= 20 else 0
        vol_expanding = vol_5 > vol_20 if vol_20 > 0 else False

        detail = (f"最新日期 {trade_date} | 上涨占比 {up_ratio:.1%} | "
                  f"5日均量 {'>' if vol_expanding else '<='} 20日均量")

        if up_ratio > 0.60 and vol_expanding:
            return "attack", MAX_POS_MAP["attack"], detail
        elif up_ratio < 0.40 and not vol_expanding:
            return "empty", MAX_POS_MAP["empty"], detail
        else:
            return "defense", MAX_POS_MAP["defense"], detail

    except Exception as e:
        log.warning("市场广度计算失败: %s", e)
        return "defense", MAX_POS_MAP["defense"], f"计算异常: {e}"



# =============================================================================
# 主接口
# =============================================================================

def get_market_mode(conn: sqlite3.Connection = None,
                    db_path: str = DB_PATH,
                    target_date: str = None,
                    persist: bool = True) -> tuple:
    """
    获取大盘环境模式。

    参数：
        conn        已有的 SQLite 连接（可选）
        db_path     数据库路径
        target_date 若传入（YYYYMMDD），所有查询截止到该日
                    （回测模式：严禁未来数据）
        persist     是否将结果写入 market_env.json
                    （回测时设 False 避免污染实盘数据）

    返回：
        (mode: str, max_pos: float, detail: str)
    """
    _own_conn = conn is None
    if _own_conn:
        try:
            conn = sqlite3.connect(db_path, check_same_thread=False)
        except Exception as e:
            log.error("DB连接失败: %s", e)
            return "defense", MAX_POS_MAP["defense"], f"DB连接失败: {e}"

    try:
        # 一级：指数法
        result = _mode_from_index(conn)

        # 二级降级：广度法（传入 target_date 履测历史截断）
        if result is None:
            result = _mode_from_market_breadth(conn, target_date=target_date)

        mode, max_pos, detail = result

        if persist:
            _save_env_json(mode, max_pos, detail)

        log.debug("大盘环境判断 [%s] → %s(%s) | %.0f%% | %s",
                  target_date or 'latest', MODE_ZH[mode], mode, max_pos * 100, detail)
        return mode, max_pos, detail

    finally:
        if _own_conn and conn:
            conn.close()


def load_market_mode() -> tuple:
    """
    从 market_env.json 读取缓存的大盘模式（供其他模块快速调用，不查DB）。
    若文件不存在或过期（超过12小时），返回 defense 默认值。

    返回：
        (mode: str, max_pos: float, detail: str)
    """
    if not os.path.exists(ENV_JSON):
        log.warning("market_env.json 不存在，使用防守默认值")
        return "defense", MAX_POS_MAP["defense"], "缓存文件不存在"

    try:
        with open(ENV_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 检查时效性（超过 12 小时视为过期）
        ts_str   = data.get("timestamp", "")
        if ts_str:
            ts = datetime.fromisoformat(ts_str)
            age_hours = (datetime.now() - ts).total_seconds() / 3600
            if age_hours > 12:
                log.warning("market_env.json 已过期（%.1f 小时），建议重新运行", age_hours)

        mode    = data.get("mode", "defense")
        max_pos = data.get("max_pos", MAX_POS_MAP.get(mode, 0.30))
        detail  = data.get("detail", "")
        return mode, max_pos, detail

    except Exception as e:
        log.warning("读取 market_env.json 失败: %s", e)
        return "defense", MAX_POS_MAP["defense"], f"读取失败: {e}"


def _save_env_json(mode: str, max_pos: float, detail: str) -> None:
    """将大盘判断结果写入 JSON 缓存文件。"""
    payload = {
        "mode":      mode,
        "mode_zh":   MODE_ZH.get(mode, mode),
        "max_pos":   max_pos,
        "detail":    detail,
        "timestamp": datetime.now().isoformat(),
        "date":      datetime.now().strftime("%Y-%m-%d"),
    }
    try:
        with open(ENV_JSON, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log.debug("market_env.json 已更新")
    except Exception as e:
        log.warning("写入 market_env.json 失败: %s", e)


# =============================================================================
# CLI 独立测试入口
# =============================================================================
if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    print("=" * 55)
    print("  StockAI v4.0 - Da Pan Huan Jing Pan Duan Ce Shi")
    print("=" * 55)

    mode, max_pos, detail = get_market_mode()

    print(f"\n  Mode     : {MODE_ZH[mode]} ({mode})")
    print(f"  Max Pos  : {max_pos * 100:.0f}%")
    print(f"  Detail   : {detail}")

    if mode == "empty":
        print("\n  [STOP] Kong cang xiu zhan, zhong zhi hou xu xuan gu liu cheng")
    elif mode == "attack":
        print("\n  [OK] Jin gong mo shi: ke ji ji can yu, cang wei ke fang da zhi 80%")
    else:
        print("\n  [DEF] Fang shou mo shi: kong zhi cang wei 30%, xuan gu geng yan ge")

    print(f"\n  Result saved -> {ENV_JSON}")

# -*- coding: utf-8 -*-
"""
market_env.py —— StockAI v4.0 大盘四色预警与仓位判断模块
=====================================================================
每日盘前/盘中运行，输出大盘环境四色定性与最高资产总仓位上限：
    green   (🟢 绿色积极进攻)  →  max_pos = 0.70  总仓位上限 70%
    yellow  (🟡 黄色谨慎参与)  →  max_pos = 0.40  总仓位上限 40%
    red     (🔴 红色防守休息)  →  max_pos = 0.20  总仓位上限 20%
    black   (⚫ 黑色极端休战)  →  max_pos = 0.00  强制空仓 0%

判断优先级：
  1. 缓存优先：从 market_env_cache 数据库表读取指定日期的缓存。
  2. 本地实时计算降级：若缓存不存在，实时查询本地 DB 进行四大客观指标分析，自动更新缓存。
  3. 实时接口/兜底降级：若本地最新数据缺失（如收盘不久未完成同步），通过 akshare 实时接口获取数据并定性，或安全定为红色 (red) 兜底。

四大指标判定：
  - 情绪 (Emotion)：连板晋级率、最高连板数、炸板率。
  - 主力 (Capital)：主力资金净额（万元）、近5日流向。
  - 宽度 (Width)：创60日新高/新低股票比值 R。
  - 指数趋势 (Trend)：上证指数连续3日低于MA60且持续放量下跌（一票否决为黑色）。
"""

import os
import json
import logging
import sqlite3
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(ROOT_DIR, "db", "stock_daily.db")
ENV_JSON = os.path.join(ROOT_DIR, "market_env.json")

log = logging.getLogger(__name__)

# 大盘四色对应最大仓位上限
MAX_POS_MAP = {
    "green":  0.70,
    "yellow": 0.40,
    "red":    0.20,
    "black":  0.00,
}

# 中文定性描述
MODE_ZH = {
    "green":  "绿色积极进攻",
    "yellow": "黄色谨慎参与",
    "red":    "红色防守休息",
    "black":  "黑色极端休战",
}


# =============================================================================
# 一级缓存读取
# =============================================================================
def _load_from_db_cache(conn: sqlite3.Connection, target_date: str) -> tuple:
    """尝试从缓存数据库表中直接读取"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT color, max_pos, detail 
            FROM market_env_cache 
            WHERE trade_date = ?
        """, (target_date,))
        row = cursor.fetchone()
        if row:
            return row[0], row[1], row[2]
    except Exception as e:
        log.debug("读取 market_env_cache 缓存表失败: %s", e)
    return None


# =============================================================================
# 二级实时计算降级
# =============================================================================
def _get_trade_days_list(conn: sqlite3.Connection, target_date: str, limit=62) -> list:
    """获取指定日期及之前的交易日列表"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT trade_date 
        FROM daily_prices 
        WHERE trade_date <= ? 
        ORDER BY trade_date DESC LIMIT ?
    """, (target_date, limit))
    rows = cursor.fetchall()
    return sorted([r[0] for r in rows])


def _compute_single_date_metrics(conn: sqlite3.Connection, target_date: str) -> tuple:
    """
    当无离线缓存时，实时在本地计算 target_date 的四色大盘环境。
    """
    try:
        # 0. 验证本地是否有足够的数据
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM daily_prices WHERE trade_date = ?", (target_date,))
        cnt = cursor.fetchone()[0]
        if cnt < 500:
            # 单日行情数太少，说明当日行情未完整同步，无法进行本地大盘分析
            return None

        # 获取前序交易日（用于连板、放量及MA60均线预热）
        trade_days = _get_trade_days_list(conn, target_date, limit=120)
        if len(trade_days) < 62:
            return None
        
        prev_date = trade_days[-2]
        pre_60_date = trade_days[-60]

        # 1. 情绪指标计算
        # 加载当日与前一日的行情 (含 stock_list 名称以判定 ST)
        df_emotion = pd.read_sql("""
            SELECT p.ts_code, p.trade_date, p.high, p.close, p.pre_close, p.pct_chg, s.name
            FROM daily_prices p
            LEFT JOIN stock_list s ON p.ts_code = s.ts_code
            WHERE p.trade_date IN (?, ?)
        """, conn, params=(prev_date, target_date))
        
        df_emotion["name"] = df_emotion["name"].fillna("")
        for col in ["close", "pre_close", "high", "pct_chg"]:
            df_emotion[col] = pd.to_numeric(df_emotion[col], errors="coerce").fillna(0)

        # 判定涨停标准
        is_kc_cy = df_emotion["ts_code"].str.startswith("300") | df_emotion["ts_code"].str.startswith("301") | df_emotion["ts_code"].str.startswith("688")
        is_bj = df_emotion["ts_code"].str.startswith("8") | df_emotion["ts_code"].str.startswith("43") | df_emotion["ts_code"].str.startswith("83")
        is_st = df_emotion["name"].str.contains("ST") | df_emotion["name"].str.contains("st")
        
        limit_pct = np.select([is_kc_cy, is_bj, is_st], [19.9, 29.9, 4.9], default=9.9)
        df_emotion["is_limit_close"] = df_emotion["pct_chg"] >= limit_pct
        
        high_pct = (df_emotion["high"] - df_emotion["pre_close"]) / df_emotion["pre_close"] * 100
        df_emotion["is_limit_touch"] = high_pct >= (limit_pct - 0.1)
        df_emotion["is_limit_open"] = df_emotion["is_limit_touch"] & (~df_emotion["is_limit_close"])

        # 提取昨日涨停名单
        limit_close_prev = df_emotion[(df_emotion["trade_date"] == prev_date) & df_emotion["is_limit_close"]]["ts_code"].tolist()
        # 提取今日封板名单
        limit_close_today = df_emotion[(df_emotion["trade_date"] == target_date) & df_emotion["is_limit_close"]]["ts_code"].tolist()
        # 提取今日曾触板与炸板数
        today_df = df_emotion[df_emotion["trade_date"] == target_date]
        limit_touch_cnt = int(today_df["is_limit_touch"].sum())
        limit_open_cnt = int(today_df["is_limit_open"].sum())
        limit_close_cnt = int(today_df["is_limit_close"].sum())

        # 计算连板高度 (简易版：向上回溯昨日是否涨停)
        # 获取股票多日的涨停记录
        history_limits = pd.read_sql("""
            SELECT ts_code, trade_date, pct_chg
            FROM daily_prices
            WHERE trade_date <= ? AND ts_code IN (
                SELECT ts_code FROM daily_prices WHERE trade_date = ? AND pct_chg >= 9.9
            )
            ORDER BY ts_code, trade_date DESC
        """, conn, params=(target_date, target_date))
        
        con_limit_map = {}
        for code, grp in history_limits.groupby("ts_code"):
            con_count = 0
            for _, r in grp.iterrows():
                # 判断当前个股在当前日期的封板比例
                c_st = "ST" in code or "st" in code # 简易判断
                c_cy = code.startswith("300") or code.startswith("301") or code.startswith("688")
                c_bj = code.startswith("8") or code.startswith("43") or code.startswith("83")
                c_limit = 19.9 if c_cy else (29.9 if c_bj else (4.9 if c_st else 9.9))
                if r["pct_chg"] >= c_limit:
                    con_count += 1
                else:
                    break
            con_limit_map[code] = con_count

        max_con_limit = max(con_limit_map.values()) if con_limit_map else 0

        # 今日连板股（今日涨停且昨日也是涨停）
        promotion_codes = [c for c in limit_close_today if c in limit_close_prev]
        promotion_cnt = len(promotion_codes)

        # 炸板率
        explode_rate = float(limit_open_cnt / limit_touch_cnt) if limit_touch_cnt > 0 else 0.0
        # 晋级率
        promotion_rate = float(promotion_cnt / len(limit_close_prev)) if len(limit_close_prev) > 0 else 0.0

        emotion_status = "neutral"
        if promotion_rate >= 0.40 and max_con_limit >= 5:
            emotion_status = "positive"
        elif promotion_rate < 0.30 or explode_rate > 0.40:
            if len(limit_close_prev) > 2:  # 昨日有连板种子时，判定晋级率退潮才有意义
                emotion_status = "negative"

        # 2. 主力资金计算
        # 计算今日主力净额与近5日流入频次
        df_cap = pd.read_sql("""
            SELECT trade_date, SUM(buy_elg_amount + buy_lg_amount - sell_elg_amount - sell_lg_amount) as net_main
            FROM moneyflow
            WHERE trade_date <= ?
            GROUP BY trade_date
            ORDER BY trade_date DESC LIMIT 5
        """, conn, params=(target_date,))
        
        if df_cap.empty:
            return None
        
        df_cap["net_main_yi"] = df_cap["net_main"] / 10000.0
        net_main_yi = float(df_cap.iloc[0]["net_main_yi"])
        inflow_5d_cnt = int((df_cap["net_main_yi"] > 0).sum())

        capital_status = "neutral"
        if net_main_yi > 0 or inflow_5d_cnt >= 3:
            capital_status = "positive"
        elif net_main_yi < -200.0:  # 流出超200亿
            capital_status = "negative"

        # 3. 市场宽度计算 (60日高低点比值)
        # 我们使用临时内存表或子查询求高低值
        # 找出 target_date 当天股票的 close，以及过去60交易日的高低
        df_width = pd.read_sql("""
            SELECT p.ts_code, p.close,
                   (SELECT MAX(close) FROM daily_prices WHERE ts_code = p.ts_code AND trade_date BETWEEN ? AND ?) as high_60d,
                   (SELECT MIN(close) FROM daily_prices WHERE ts_code = p.ts_code AND trade_date BETWEEN ? AND ?) as low_60d
            FROM daily_prices p
            WHERE p.trade_date = ? AND p.close IS NOT NULL
        """, conn, params=(pre_60_date, target_date, pre_60_date, target_date, target_date))
        
        df_width["is_high"] = df_width["close"] == df_width["high_60d"]
        df_width["is_low"] = df_width["close"] == df_width["low_60d"]
        new_high_cnt = int(df_width["is_high"].sum())
        new_low_cnt = int(df_width["is_low"].sum())
        ratio_R = float(new_high_cnt / new_low_cnt) if new_low_cnt > 0 else float(new_high_cnt)

        width_status = "neutral"
        if ratio_R > 1.5:
            width_status = "positive"
        elif ratio_R < 0.8:
            width_status = "negative"

        # 4. 指数趋势判定 (MA60 连续3日放量下跌)
        # 上证指数近120日行情
        df_idx = pd.read_sql("""
            SELECT trade_date, close, vol, pct_chg
            FROM daily_index
            WHERE ts_code = '000001.SH' AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT 120
        """, conn, params=(target_date,))
        
        if len(df_idx) < 62:
            return None
        
        # 翻转为时序正序
        df_idx = df_idx.iloc[::-1].reset_index(drop=True)
        df_idx["close"] = pd.to_numeric(df_idx["close"], errors="coerce")
        df_idx["vol"] = pd.to_numeric(df_idx["vol"], errors="coerce")
        df_idx["ma60"] = df_idx["close"].rolling(60).mean()

        latest_3 = df_idx.tail(3)
        below_ma60 = (latest_3["close"] < latest_3["ma60"]).all()
        # 持续下跌：第3天价<第2天价<第1天价
        prices = latest_3["close"].tolist()
        drops = prices[2] < prices[1] < prices[0] or (latest_3["pct_chg"] < 0).all()
        # 持续放量：第3天量>第2天量>第1天量
        vols = latest_3["vol"].tolist()
        vol_expanding = vols[2] > vols[1] > vols[0]

        trend_status = "normal"
        if below_ma60 and drops and vol_expanding:
            trend_status = "risk"

        # 5. 整合判定颜色
        if trend_status == "risk":
            color = "black"
        elif emotion_status == "negative" or capital_status == "negative" or width_status == "negative":
            color = "red"
        elif emotion_status == "positive" and capital_status == "positive":
            color = "green"
        elif emotion_status == "positive" or capital_status == "positive":
            color = "yellow"
        else:
            color = "red"  # 双中性默认红色防守

        max_pos = MAX_POS_MAP[color]
        
        # 包装详情
        detail_dict = {
            "limit_close_cnt": limit_close_cnt,
            "limit_touch_cnt": limit_touch_cnt,
            "limit_open_cnt": limit_open_cnt,
            "max_con_limit": max_con_limit,
            "explode_rate": round(explode_rate, 4),
            "promotion_rate": round(promotion_rate, 4),
            "net_main_yi": round(net_main_yi, 2),
            "inflow_5d_cnt": inflow_5d_cnt,
            "new_high_cnt": new_high_cnt,
            "new_low_cnt": new_low_cnt,
            "ratio_R": round(ratio_R, 2),
            "sh_close": float(latest_3.iloc[-1]["close"]),
            "sh_ma60": float(latest_3.iloc[-1]["ma60"]),
            "sh_vol": float(latest_3.iloc[-1]["vol"])
        }
        detail_json = json.dumps(detail_dict, ensure_ascii=False)

        # 自动回填更新缓存表
        cursor.execute("""
            INSERT OR REPLACE INTO market_env_cache (
                trade_date, emotion_status, capital_status, width_status, trend_status, color, max_pos, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (target_date, emotion_status, capital_status, width_status, trend_status, color, max_pos, detail_json))
        conn.commit()
        log.info(f"[COMPUTE] 成功实时计算并缓存今日 {target_date} 指标 -> {MODE_ZH[color]}")

        return color, max_pos, detail_json

    except Exception as e:
        log.error("本地实时大盘指标计算发生异常: %s", e)
        import traceback
        traceback.print_exc()
    return None


# =============================================================================
# 三级实时接口/降级方案
# =============================================================================
def _fallback_live_judgement(target_date: str) -> tuple:
    """
    当本地 DB 缺失行情时（例如盘后暂未同步），通过 akshare 实时数据源获取进行降级研判。
    """
    log.warning(f"[FALLBACK] 本地数据不全，尝试通过实时 akshare 接口进行 {target_date} 大盘环境定性...")
    try:
        import akshare as ak
        
        # 1. 抓取今日涨停和炸板股池
        try:
            df_zt = ak.stock_zt_pool_em(date=target_date)
            df_zb = ak.stock_zt_pool_zbgc_em(date=target_date)
            len_zt = len(df_zt) if df_zt is not None else 0
            len_zb = len(df_zb) if df_zb is not None else 0
            
            explode_rate = len_zb / (len_zt + len_zb) if (len_zt + len_zb) > 0 else 0.0
            max_con = int(df_zt["连板数"].max()) if len_zt > 0 and "连板数" in df_zt.columns else 0
            
            # 由于没有昨日股票，我们将晋级率简化为今日封板中连板比例或设为中性
            promotion_rate = 0.35 # 默认中性
            if len_zt > 0 and "连板数" in df_zt.columns:
                con_cnt = (df_zt["连板数"] >= 2).sum()
                promotion_rate = con_cnt / len_zt
            
            emotion_status = "neutral"
            if promotion_rate >= 0.40 and max_con >= 5:
                emotion_status = "positive"
            elif promotion_rate < 0.30 or explode_rate > 0.40:
                emotion_status = "negative"
        except Exception as e:
            log.warning("akshare 情绪指标拉取失败: %s", e)
            emotion_status = "neutral"
            explode_rate, max_con, promotion_rate = 0.0, 0, 0.0

        # 2. 上证指数实时情况
        try:
            # 抓取上证日线
            df_idx = ak.stock_zh_index_daily_em(symbol="sh000001")
            df_idx = df_idx.sort_values("date").tail(65).copy()
            df_idx["close"] = pd.to_numeric(df_idx["close"])
            df_idx["vol"] = pd.to_numeric(df_idx["volume"])
            df_idx["ma60"] = df_idx["close"].rolling(60).mean()
            
            latest = df_idx.iloc[-1]
            close_now = latest["close"]
            ma60_now = latest["ma60"]
            
            below_ma60 = (df_idx.tail(3)["close"] < df_idx.tail(3)["ma60"]).all()
            
            trend_status = "normal"
            if below_ma60 and (df_idx.tail(3)["close"].diff().fillna(0).tail(2) < 0).all():
                # 连续收阴，简单标记
                trend_status = "risk"
        except Exception as e:
            log.warning("akshare 上证指数拉取失败: %s", e)
            close_now, ma60_now = 3000.0, 3000.0
            trend_status = "normal"

        # 3. 主力资金（极速降级：若指数未大跌，按中性处理；若指数低于MA60且主力在抛售，定为红色）
        capital_status = "neutral"
        if close_now < ma60_now:
            capital_status = "negative"  # 指数在均线下方，资金面偏防守

        # 整合判定
        if trend_status == "risk":
            color = "black"
        elif emotion_status == "negative" or capital_status == "negative":
            color = "red"
        elif emotion_status == "positive" and capital_status == "positive":
            color = "green"
        else:
            color = "yellow"

        max_pos = MAX_POS_MAP[color]
        detail_dict = {
            "source": "akshare_fallback",
            "sh_close": float(close_now),
            "sh_ma60": float(ma60_now),
            "explode_rate": round(explode_rate, 4),
            "max_con_limit": max_con,
            "promotion_rate": round(promotion_rate, 4)
        }
        log.info(f"[FALLBACK] akshare 成功定性今日大盘为 -> {MODE_ZH[color]}")
        return color, max_pos, json.dumps(detail_dict, ensure_ascii=False)

    except Exception as e:
        log.error(f"akshare 降级分析失败，使用极端兜底方案 (红色-20%%): {e}")
        # 终极兜底：定为红色 (防守状态，限仓 20%)
        return "red", 0.20, json.dumps({"source": "ultimate_red_fallback", "error": str(e)}, ensure_ascii=False)


# =============================================================================
# 主接口
# =============================================================================
def get_market_mode(conn: sqlite3.Connection = None,
                    db_path: str = DB_PATH,
                    target_date: str = None,
                    persist: bool = True) -> tuple:
    """
    获取当前大盘四色状态与最高总仓位。

    参数：
        conn        已有的 SQLite 连接（可选）
        db_path     数据库路径
        target_date 指定交易日（格式: YYYYMMDD），回测用，为空时默认为今日
        persist     是否保存结果到本地 market_env.json

    返回：
        (color: str, max_pos: float, detail: str)
        例如: ("green", 0.70, "{...}")
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y%m%d")
    
    _own_conn = conn is None
    if _own_conn:
        try:
            conn = sqlite3.connect(db_path, check_same_thread=False)
        except Exception as e:
            log.error("DB连接失败: %s", e)
            return "red", 0.20, f"DB连接失败: {e}"

    try:
        # 1. 缓存优先
        result = _load_from_db_cache(conn, target_date)
        
        # 2. 本地实时计算降级
        if result is None:
            result = _compute_single_date_metrics(conn, target_date)
            
        # 3. 实时接口/兜底降级 (只对今天进行降级，防止回测漏数据导致未来偏差)
        if result is None:
            today_str = datetime.now().strftime("%Y%m%d")
            if target_date >= today_str:
                result = _fallback_live_judgement(target_date)
            else:
                # 历史日期因缺少指数/资金数据而无法计算，为确保回测继续，采用默认红色防守
                log.warning(f"[WARN] 历史交易日 {target_date} 数据不足且无缓存，默认定性为红色(red)")
                result = "red", 0.20, json.dumps({"source": "historical_fallback_red"}, ensure_ascii=False)

        color, max_pos, detail = result

        if persist:
            _save_env_json(color, max_pos, detail)

        return color, max_pos, detail

    finally:
        if _own_conn and conn:
            conn.close()


def load_market_mode() -> tuple:
    """
    从 market_env.json 中快速加载缓存结果（免DB）。
    若过期（超过12小时），建议外部触发重算。
    """
    if not os.path.exists(ENV_JSON):
        log.warning("market_env.json 不存在，默认返回红色防守")
        return "red", 0.20, "缓存文件不存在"

    try:
        with open(ENV_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)

        ts_str = data.get("timestamp", "")
        if ts_str:
            ts = datetime.fromisoformat(ts_str)
            age_hours = (datetime.now() - ts).total_seconds() / 3600
            if age_hours > 12:
                log.warning("market_env.json 缓存已过期 (%.1f 小时)", age_hours)

        color = data.get("mode", "red")
        max_pos = data.get("max_pos", MAX_POS_MAP.get(color, 0.20))
        detail = data.get("detail", "")
        return color, max_pos, detail

    except Exception as e:
        log.warning("读取 market_env.json 失败: %s", e)
        return "red", 0.20, f"读取失败: {e}"


def _save_env_json(color: str, max_pos: float, detail: str) -> None:
    """将判定结果缓存入 JSON 供外部模块使用"""
    payload = {
        "mode":      color,
        "mode_zh":   MODE_ZH.get(color, color),
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
# CLI 测试入口
# =============================================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="指定日期 YYYYMMDD")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    date_str = args.date or datetime.now().strftime("%Y%m%d")
    color, max_pos, detail = get_market_mode(target_date=date_str)
    
    print("\n" + "=" * 55)
    print(f" 大盘四色预警研判结果 [{date_str}]")
    print("=" * 55)
    print(f"  状态颜色 : {MODE_ZH[color]} ({color})")
    print(f"  最高总仓 : {max_pos * 100:.0f}%")
    print(f"  详情指标 : {detail}")
    print("=" * 55)

# -*- coding: utf-8 -*-
"""
main.py —— StockAI Funnel 三层漏斗主程序 (v4.0)
=====================================================================
【v4.0 核心优化】
  1. pre_screen()        SQL 预筛选，只对"涨幅活跃 + 换手率达标"的股票打分
                         避免全市场 5000+ 只股票串行遍历，速度提升 10-50x
  2. score_batch()       ThreadPoolExecutor 并行打分（CPU-bound 评分函数）
  3. check_ollama()      启动前健康检查，Ollama 未起 → 红色报错立即退出
                         不再因 AI 服务宕机而空转浪费时间

【完整漏斗架构】
  pre_screen()   SQL 预筛选（全市场 → 数百只活跃股）
    ↓
  score_batch()  并行打分（数百只 → Top-50 候选股）
    ↓
  check_ollama() + run_ai_analysis_parallel()  AI 分析（仅 score≥80 的股票）
    ↓
  push_to_feishu()  飞书交互卡片推送

【关键参数】
  AI_TRIGGER_SCORE = 80    低于此分不允许调用 Ollama
  PRE_SCREEN_PCT   = 1.0   pre_screen 涨幅下限（%），低于此值跳过
  PRE_SCREEN_TURN  = 2.0   pre_screen 换手率下限（%），低于此值跳过
  TOP_N            = 50    打分后取 Top-N 进入第二层
  MAX_CONCURRENT   = 3     Ollama 最大并发数（防显存溢出）
  SCORE_WORKERS    = 8     打分阶段并行线程数（CPU 密集型）
"""

import io
import os
import sys
import time
import sqlite3
import logging
import concurrent.futures
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

# Windows GBK 控制台 → 强制 UTF-8
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

# ── ANSI 颜色（用于控制台红色/绿色报错提示）─────────────────────────────────
# Windows 10+ 支持 ANSI，兼容性兜底：若不支持则回退为普通文本
_RED    = "\033[91m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"

def _cprint(color: str, msg: str) -> None:
    """带颜色的控制台输出（自动兼容不支持 ANSI 的终端）"""
    try:
        print(color + msg + _RESET, flush=True)
    except Exception:
        print(msg, flush=True)

# ── 日志配置 ──────────────────────────────────────────────────────────────────
LOG_DIR = os.path.join(ROOT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(
            os.path.join(LOG_DIR, f"main_{datetime.now().strftime('%Y%m%d')}.log"),
            encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("main")


# =============================================================================
# 全局参数（可通过 config.py 覆盖）
# =============================================================================
try:
    from config import FILTER_CONFIG, AI_CONFIG, DB_PATH
    AI_TRIGGER_SCORE = AI_CONFIG.get("trigger_score", 80)
    MAX_CONCURRENT   = AI_CONFIG.get("max_concurrent", 3)
    TOP_N            = FILTER_CONFIG.get("top_n", 50)
except ImportError:
    AI_TRIGGER_SCORE = 80
    MAX_CONCURRENT   = 3
    TOP_N            = 50
    DB_PATH          = os.path.join(ROOT_DIR, "db", "stock_daily.db")

# pre_screen 筛选阈值（可按需调整）
PRE_SCREEN_PCT_MIN   = 1.0    # 涨幅下限（%），低于此值跳过 → 过滤死水股
PRE_SCREEN_PCT_MAX   = 9.5    # 涨幅上限（%），高于此值为涨停，跳过追高风险
PRE_SCREEN_TURN_MIN  = 2.0    # 换手率下限（%），低于此值视为不活跃
PRE_SCREEN_AMOUNT    = 50000  # 成交额下限（千元 = 5000万），Tushare 单位
SCORE_WORKERS        = 8      # 打分阶段线程数（根据 CPU 核心数调整）

OLLAMA_HOST = "http://localhost:11434"  # Ollama 服务地址


# =============================================================================
# ★ 新增功能 1：Ollama 健康检查
# =============================================================================

def check_ollama(exit_on_fail: bool = True) -> bool:
    """
    在调用 AI 之前，先 ping Ollama 的 /api/tags 接口确认服务是否就绪。

    【为什么要做健康检查】
    若 Ollama 未启动，之前的版本会在每只股票上等待 10 秒超时后才失败，
    50 只股票 × 10 秒 = 500 秒纯粹的空转浪费。
    提前 1 次 HTTP 检查，即可在 2 秒内确定是否能继续。

    参数：
        exit_on_fail  True = 检查失败时直接终止进程（最常用）
                      False = 仅返回 False，由调用方决定如何处理

    返回：
        True  = Ollama 正常运行
        False = Ollama 不可达（仅当 exit_on_fail=False 时才返回 False）
    """
    import requests

    check_url = f"{OLLAMA_HOST}/api/tags"
    _cprint(_BOLD, f"[健康检查] 正在 ping Ollama ({check_url})...")

    try:
        resp = requests.get(check_url, timeout=2)
        resp.raise_for_status()
        data = resp.json()

        # 检查模型是否已加载（/api/tags 返回 {"models": [...]}）
        models = [m.get("name", "") for m in data.get("models", [])]
        target_model = "qwen2.5:7b-instruct-q4_K_M"

        if not models:
            _cprint(_YELLOW, f"  ⚠️  Ollama 已启动但尚未加载任何模型")
            _cprint(_YELLOW, f"     请执行: ollama pull {target_model}")
        else:
            # 检查目标模型是否在列表中（模糊匹配前缀）
            matched = any(target_model.split(":")[0] in m for m in models)
            if matched:
                _cprint(_GREEN, f"  ✅ Ollama 正常 | 模型: {models}")
            else:
                _cprint(_YELLOW, f"  ⚠️  已加载模型: {models}")
                _cprint(_YELLOW, f"     目标模型 {target_model} 未找到，将使用已有模型")

        return True

    except requests.exceptions.ConnectionError:
        # Ollama 完全未启动
        _cprint(_RED, "")
        _cprint(_RED, "  ╔══════════════════════════════════════════════════════╗")
        _cprint(_RED, "  ║   ❌  OLLAMA 服务未启动！AI 分析无法进行             ║")
        _cprint(_RED, "  ║                                                      ║")
        _cprint(_RED, "  ║   解决方案：                                         ║")
        _cprint(_RED, f"  ║   1. 打开终端，执行: ollama serve                   ║")
        _cprint(_RED, f"  ║   2. 确认模型已下载: ollama pull {target_model[:20]}...   ║")
        _cprint(_RED, "  ║   3. 重新运行本程序                                  ║")
        _cprint(_RED, "  ╚══════════════════════════════════════════════════════╝")
        _cprint(_RED, "")

        if exit_on_fail:
            log.critical("Ollama 服务未启动，程序强制退出（避免空转浪费时间）")
            sys.exit(1)  # 非零退出码，方便脚本/调度器捕获
        return False

    except requests.exceptions.Timeout:
        _cprint(_RED, f"  ❌  Ollama 连接超时（{OLLAMA_HOST} 响应 > 3s）")
        _cprint(_RED, "     可能正在启动中，请稍后重试")
        if exit_on_fail:
            sys.exit(1)
        return False

    except Exception as e:
        _cprint(_RED, f"  ❌  Ollama 健康检查失败：{e}")
        if exit_on_fail:
            sys.exit(1)
        return False


# =============================================================================
# ★ 新增功能 2：SQL 预筛选（pre_screen）
# =============================================================================

def pre_screen(
    conn: sqlite3.Connection,
    pct_min: float = PRE_SCREEN_PCT_MIN,
    pct_max: float = PRE_SCREEN_PCT_MAX,
    turn_min: float = PRE_SCREEN_TURN_MIN,
    amount_min: float = PRE_SCREEN_AMOUNT,
    trade_date: str = None,
    industry_filter: List[str] = None,
) -> List[Dict]:
    """
    使用 SQL 联表查询进行预筛选，仅返回"涨幅活跃 + 换手率达标"的股票列表。

    【为什么用 SQL 而不是 Python 循环】
    全市场 5000+ 只股票，若逐只读取 Python 评分，即使每只只需 10ms，
    也需要 50 秒以上。SQL 在数据库内部完成过滤，只返回满足条件的记录，
    速度可快 10-50 倍（通常 < 2 秒完成）。

    【筛选条件说明】
    - pct_chg BETWEEN pct_min AND pct_max：
        只看有上涨动能的股票（剔除跌停、阴跌、死水股）
    - turnover_rate >= turn_min：
        换手活跃，说明资金在参与（剔除无人交易的僵尸股）
    - amount >= amount_min：
        成交额 >= 5000 万，流动性基本保障
    - name NOT LIKE '%ST%'：
        剔除 ST 股（直接在 SQL 层过滤，不走 Python）
    - list_date <= 3 个月前：
        剔除次新股（上市不足 3 个月的新股波动异常）

    参数：
        conn             SQLite 连接对象
        pct_min          涨幅下限（%）
        pct_max          涨幅上限（%），防追涨停
        turn_min         换手率下限（%）
        amount_min       成交额下限（千元，Tushare 单位）
        trade_date       查询日期（YYYYMMDD），默认取数据库最新交易日
        industry_filter  行业白名单列表（如 ['半导体','消费电子']）。
                         传入则只扫描该行业，不传则全市场扫描（向后兼容）。

    返回：
        List[Dict]，每条包含: ts_code / name / industry / pct_chg /
                              turnover_rate / volume_ratio / amount / trade_date
    """
    # ── 1. 确定查询日期（取数据库最新交易日，而非系统今天）─────────────────────
    if trade_date is None:
        row = conn.execute(
            "SELECT MAX(trade_date) FROM daily_prices"
        ).fetchone()
        trade_date = row[0] if row and row[0] else datetime.now().strftime("%Y%m%d")

    # 次新股剔除：上市时间 <= 90 天的股票
    new_stock_cutoff = (datetime.strptime(trade_date, "%Y%m%d")
                        - timedelta(days=90)).strftime("%Y%m%d")

    log.info("pre_screen: 查询日期=%s | 涨幅[%.1f%%, %.1f%%] | 换手率>=%.1f%% | 成交额>=%.0f千元%s",
             trade_date, pct_min, pct_max, turn_min, amount_min,
             f" | 行业过滤: {industry_filter}" if industry_filter else "")

    # ── 行业过滤子句（动态拼接，避免 SQL 注入：用参数绑定）──────────────────
    industry_clause = ""
    industry_params: List[str] = []
    if industry_filter:
        placeholders = ",".join("?" * len(industry_filter))
        industry_clause = f"AND sl.industry IN ({placeholders})"
        industry_params = list(industry_filter)

    # ── 2. 核心 SQL：三表联查 ────────────────────────────────────────────────
    # daily_prices   → 涨幅、成交额
    # daily_basic    → 换手率、量比（LEFT JOIN，数据可能缺失）
    # stock_list     → 股票名称、行业、ST 过滤、次新股过滤
    #
    # 注意：daily_basic 用 LEFT JOIN，因为部分股票可能无当日 daily_basic 记录；
    #       若 turnover_rate IS NULL，则不施加换手率过滤（宽松处理）。
    sql = """
    SELECT
        dp.ts_code,
        sl.name,
        sl.industry,
        dp.trade_date,
        dp.pct_chg,
        dp.amount,
        dp.vol,
        COALESCE(db.turnover_rate, -1)  AS turnover_rate,
        COALESCE(db.volume_ratio,  -1)  AS volume_ratio
    FROM daily_prices dp
    INNER JOIN stock_list sl
        ON dp.ts_code = sl.ts_code
    LEFT JOIN daily_basic db
        ON dp.ts_code = db.ts_code
        AND dp.trade_date = db.trade_date
    WHERE
        dp.trade_date = :trade_date

        -- 涨幅活跃区间（剔除跌停、阴跌、涨停追高）
        AND dp.pct_chg BETWEEN :pct_min AND :pct_max

        -- 成交额下限（Tushare amount 单位为千元，5000万=50000千元）
        AND dp.amount >= :amount_min

        -- 换手率下限（若 daily_basic 无数据则放行，避免漏掉好股票）
        AND (db.turnover_rate IS NULL OR db.turnover_rate >= :turn_min)

        -- 剔除 ST 股（直接在 SQL 层过滤，效率最高）
        AND sl.name NOT LIKE '%ST%'
        AND sl.name NOT LIKE '%st%'

        -- 剔除次新股（上市不足 90 天）
        AND sl.list_date <= :new_stock_cutoff

        {industry_clause}

    ORDER BY dp.pct_chg DESC, dp.amount DESC
    """.format(industry_clause=industry_clause)

    # 全部改用 positional 参数（?），行业过滤的 IN 列表参数追加到末尾
    pos_params = (
        trade_date, pct_min, pct_max, amount_min, turn_min, new_stock_cutoff,
        *industry_params
    )

    # 将 SQL 中的 named params 改为 positional(?)
    sql_pos = sql.replace(":trade_date",      "?").replace(":pct_min",         "?") \
                 .replace(":pct_max",         "?").replace(":amount_min",      "?") \
                 .replace(":turn_min",        "?").replace(":new_stock_cutoff","?")

    start_t = time.time()
    try:
        cursor = conn.execute(sql_pos, pos_params)
        columns = [d[0] for d in cursor.description]
        rows    = cursor.fetchall()
    except sqlite3.OperationalError as e:
        log.warning("pre_screen SQL 执行失败（可能缺少 daily_basic 表）：%s", e)
        # 降级：只用 daily_prices + stock_list，不要 turnover_rate
        sql_fallback = """
        SELECT
            dp.ts_code,
            sl.name,
            sl.industry,
            dp.trade_date,
            dp.pct_chg,
            dp.amount,
            dp.vol,
            -1 AS turnover_rate,
            -1 AS volume_ratio
        FROM daily_prices dp
        INNER JOIN stock_list sl ON dp.ts_code = sl.ts_code
        WHERE
            dp.trade_date = ?
            AND dp.pct_chg BETWEEN ? AND ?
            AND dp.amount  >= ?
            AND sl.name NOT LIKE '%ST%'
            AND sl.list_date <= ?
            {industry_clause}
        ORDER BY dp.pct_chg DESC, dp.amount DESC
        """.format(industry_clause=industry_clause)
        fb_params = (trade_date, pct_min, pct_max, amount_min, new_stock_cutoff,
                     *industry_params)
        cursor  = conn.execute(sql_fallback, fb_params)
        columns = [d[0] for d in cursor.description]
        rows    = cursor.fetchall()

    elapsed = time.time() - start_t
    results = [dict(zip(columns, row)) for row in rows]

    log.info("pre_screen 完成：%s扫描 → %d 只活跃股（SQL耗时 %.2fs）",
             f"行业[{','.join(industry_filter)}]" if industry_filter else "全市场",
             len(results), elapsed)

    return results


# =============================================================================
# ★ 新增功能 3：并行打分（score_batch）
# =============================================================================

def _score_one_worker(stock_meta: Dict, conn_path: str) -> Optional[Dict]:
    """
    单只股票打分的 worker 函数（在线程中运行）。

    【为什么每个 worker 创建独立连接】
    SQLite 连接不是线程安全的（check_same_thread=True 是默认值）。
    每个线程必须有自己的连接对象，不能共享主线程的 conn。
    使用 check_same_thread=False 虽然可以共享，但在写入时有竞态风险；
    对于只读查询，每线程独立连接是最稳妥的做法。

    参数：
        stock_meta  pre_screen 返回的单条股票基础数据
        conn_path   SQLite 数据库文件路径

    返回：
        打分结果字典（包含 score / details / 技术指标），None 表示评分失败
    """
    # 每个线程独立创建 SQLite 连接
    try:
        conn = sqlite3.connect(conn_path, check_same_thread=True)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
    except Exception as e:
        log.debug("打分 worker 连接失败 %s: %s", stock_meta.get("ts_code"), e)
        return None

    try:
        # 调用 filter_engine 中的单只打分函数
        from filter_engine import score_one, FILTER_CONFIG as FC
        result = score_one(
            ts_code=stock_meta["ts_code"],
            name=stock_meta.get("name", ""),
            industry=stock_meta.get("industry", ""),
            conn=conn,
            cfg=FC,
        )
        if result is None:
            return None

        # 将 pre_screen 已取到的字段合并进结果（避免重复查库）
        result.setdefault("trade_date",    stock_meta.get("trade_date", ""))
        result.setdefault("pct_chg",       stock_meta.get("pct_chg", 0))
        result.setdefault("amount_raw",    stock_meta.get("amount", 0))
        result.setdefault("turnover_rate", stock_meta.get("turnover_rate", -1))
        result.setdefault("volume_ratio",  stock_meta.get("volume_ratio", -1))
        return result

    except Exception as e:
        log.debug("打分失败 %s: %s", stock_meta.get("ts_code", "?"), e)
        return None
    finally:
        conn.close()


def score_batch(
    prescreened: List[Dict],
    top_n: int = TOP_N,
    workers: int = SCORE_WORKERS,
) -> List[Dict]:
    """
    使用 ThreadPoolExecutor 对 pre_screen 筛出的候选股进行并行打分。

    【并行策略】
    pre_screen 已将全市场 5000 只缩减到 数百只活跃股，
    再用 SCORE_WORKERS=8 线程并行打分，每只评分约 20-50ms（SQL I/O），
    总耗时 ≈ max(单只耗时) × ceil(数量 / workers)，而非串行的 数量 × 单只耗时。

    示例：200 只股票，串行约 4-10 秒，并行约 0.5-1.5 秒。

    参数：
        prescreened  pre_screen() 的输出列表
        top_n        打分后取前 N 名（按 score 降序）
        workers      并行线程数

    返回：
        按 score 降序的 Top-N 候选股列表
    """
    if not prescreened:
        log.warning("score_batch: 输入为空，跳过打分")
        return []

    total   = len(prescreened)
    start_t = time.time()
    results = []
    errors  = 0

    log.info("score_batch: 并行打分 %d 只股票，线程数 %d...", total, workers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        # 提交所有任务：每只股票一个 worker
        future_map = {
            executor.submit(_score_one_worker, s, DB_PATH): s
            for s in prescreened
        }

        # 按完成顺序收集（as_completed 让先完成的先入队，减少等待）
        for future in concurrent.futures.as_completed(future_map):
            try:
                r = future.result(timeout=30)   # 单只打分超时 30 秒
                if r is not None:
                    results.append(r)
            except concurrent.futures.TimeoutError:
                errors += 1
                log.debug("打分超时：%s", future_map[future].get("ts_code", "?"))
            except Exception as e:
                errors += 1
                log.debug("打分异常：%s", e)

    elapsed = time.time() - start_t

    # 按分数降序，取 Top-N
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    top_results = results[:top_n]

    log.info(
        "score_batch 完成：%d 只输入 → %d 只有效 → Top-%d 输出 | "
        "耗时 %.2fs | 错误 %d",
        total, len(results), len(top_results), elapsed, errors
    )

    if top_results:
        log.info("Top-3 预览: %s",
                 " / ".join(f"{r['ts_code']}({r['score']}分)"
                            for r in top_results[:3]))

    return top_results


# =============================================================================
# 第二层：带严格门槛的 AI 分析（与 v3.0 一致，新增 Ollama 健康检查）
# =============================================================================

def _analyze_one_stock(
    stock: Dict,
    market_volume_status: str = "放量",
    sector_risk: str = "正常",
) -> Dict:
    """
    对单只股票调用 Ollama 进行 AI 深度分析。

    ⚠️  调用此函数前必须已通过 check_ollama() 确认服务可用。
    ⚠️  score < AI_TRIGGER_SCORE 的股票不会到达此函数（由调用方过滤）。
    """
    from scripts.ai_report import generate_report

    ts_code = stock.get("ts_code", "")
    score   = stock.get("score", 0)

    # 防御性二次校验（永远不相信调用方的承诺）
    if score < AI_TRIGGER_SCORE:
        log.debug("二次拦截 %s（%d < %d），跳过 Ollama", ts_code, score, AI_TRIGGER_SCORE)
        return {
            "ts_code": ts_code, "name": stock.get("name", ""),
            "python_score": score, "ai_score": 0,
            "total_score": score, "grade": "C",
            "report_md": "", "skipped": True,
        }

    log.info("=> AI 分析：%s %s（%d 分）", ts_code, stock.get("name", ""), score)

    data_json = {
        "ts_code":       ts_code,
        "name":          stock.get("name", ""),
        "industry":      stock.get("industry", ""),
        "trade_date":    stock.get("trade_date", datetime.now().strftime("%Y%m%d")),
        "close":         stock.get("close", 0),
        "pct_chg":       stock.get("pct_chg", 0),
        "python_score":  score,
        "score_details": stock.get("details", {}),
        # 附加技术指标丰富 AI 上下文
        "rsi":           stock.get("rsi"),
        "macd_dif":      stock.get("macd_dif"),
        "macd_dea":      stock.get("macd_dea"),
        "ma5":           stock.get("ma5"),
        "ma20":          stock.get("ma20"),
        "vol_ratio":     stock.get("vol_ratio"),
        "amplitude_20d": stock.get("amplitude_20d"),
        "turnover_rate": stock.get("turnover_rate"),
        "volume_ratio":  stock.get("volume_ratio"),
    }

    return generate_report(
        data_json=data_json,
        market_volume_status=market_volume_status,
        sector_risk=sector_risk,
    )


def run_ai_analysis_parallel(
    candidates: List[Dict],
    market_volume_status: str = "放量",
    sector_risk: str = "正常",
) -> Tuple[List[Dict], int]:
    """
    线程池并发 AI 分析，MAX_CONCURRENT 控制 Ollama 显存占用。
    返回 (results, skipped_count)。
    """
    eligible = [s for s in candidates if s.get("score", 0) >= AI_TRIGGER_SCORE]
    skipped  = len(candidates) - len(eligible)

    if skipped > 0:
        log.info("第二层门槛拦截：%d 只 < %d分 被阻止进入 Ollama",
                 skipped, AI_TRIGGER_SCORE)

    if not eligible:
        log.info("无股票满足 AI 门槛（>=%d分），本次不调用 Ollama", AI_TRIGGER_SCORE)
        return [], skipped

    log.info("并发 AI 分析：%d 只股票，最大并发 %d（显存保护）",
             len(eligible), MAX_CONCURRENT)

    results = []
    start_t = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as executor:
        future_map = {
            executor.submit(_analyze_one_stock, s, market_volume_status, sector_risk): s
            for s in eligible
        }
        for future in concurrent.futures.as_completed(future_map):
            stock = future_map[future]
            try:
                r = future.result(timeout=150)
                if r and not r.get("skipped"):
                    results.append(r)
                    log.info("  完成 %s %s → 总分 %d，评级 %s",
                             r["ts_code"], r.get("name",""), r["total_score"], r["grade"])
            except concurrent.futures.TimeoutError:
                log.warning("AI 超时：%s", stock.get("ts_code", ""))
            except Exception as e:
                log.warning("AI 失败 %s：%s", stock.get("ts_code", ""), e)

    elapsed = time.time() - start_t
    log.info("AI 分析完成：%d 只 | 耗时 %.0fs | 平均 %.0fs/只",
             len(results), elapsed, elapsed / max(len(results), 1))

    results.sort(key=lambda x: x.get("total_score", 0), reverse=True)
    return results, skipped


# =============================================================================
# 第三层：飞书推送（与 v3.0 一致）
# =============================================================================

def push_to_feishu(
    ai_results: List[Dict],
    filter_results: List[Dict],
    session_name: str = "手动触发",
    total_scanned: int = 0,
    trade_date: str = "",
) -> None:
    """将 AI 分析结果推送为飞书交互卡片（汇总卡片 + 个股详情卡片）。"""
    from scripts.feishu_bot import send_daily_summary, send_stock_report

    # 获取 trade_date（从第一条结果获取）
    if not trade_date and filter_results:
        trade_date = filter_results[0].get("trade_date", "")

    push_list = [
        r for r in ai_results
        if r.get("total_score", 0) >= 80 and r.get("report_md", "")
    ]

    log.info("第三层推送：AI结果 %d 只 → 推送 %d 只（≥80分）",
             len(ai_results), len(push_list))

    # 1. 汇总卡片
    summary_data = [
        {"ts_code": r["ts_code"], "name": r["name"],
         "industry": r.get("industry", ""),
         "total_score": r["total_score"], "grade": r["grade"]}
        for r in push_list
    ]
    send_daily_summary(
        results=summary_data,
        session_name=session_name,
        total_scanned=total_scanned,
        trade_date=trade_date,
    )
    time.sleep(0.5)

    # 2. 个股详情卡片
    for r in push_list:
        filter_data = next(
            (f for f in filter_results if f["ts_code"] == r["ts_code"]), {}
        )
        try:
            send_stock_report(
                ts_code=r["ts_code"], name=r["name"],
                total_score=r["total_score"],
                python_score=r.get("python_score", 0),
                ai_score=r.get("ai_score", 0),
                report_md=r.get("report_md", ""),
                industry=r.get("industry", filter_data.get("industry", "")),
                close_price=filter_data.get("close", 0.0),
                pct_chg=filter_data.get("pct_chg", 0.0),
                session_name=session_name,
                trade_date=trade_date,
            )
            log.info("  推送：%s %s（%d分）", r["ts_code"], r["name"], r["total_score"])
        except Exception as e:
            log.warning("  推送失败 %s：%s", r["ts_code"], e)
        time.sleep(0.5)


# =============================================================================
# 主流程编排（v4.0）
# =============================================================================

def main(
    session_name: str = "手动触发",
    market_volume_status: str = "放量",
    sector_risk: str = "正常",
    industry_filter: List[str] = None,   # v4.0 新增：行业白名单（None=全市场）
) -> bool:
    """
    v4.0 完整三层漏斗流程：
      [pre_screen SQL筛选] → [score_batch 并行打分] →
      [check_ollama健康检查] → [AI并发分析] → [飞书卡片推送]
    """
    t_start = time.time()

    _cprint(_BOLD, "\n" + "═" * 65)
    _cprint(_GREEN, f"  StockAI Funnel v4.0 启动 — {session_name}")
    _cprint(_BOLD, f"  时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _cprint(_BOLD, f"  AI门槛：≥{AI_TRIGGER_SCORE}分 | Ollama并发：{MAX_CONCURRENT} | Top-N：{TOP_N}")
    _cprint(_BOLD, "═" * 65 + "\n")

    # ── 打开共享 DB 连接（仅用于 pre_screen，只读）────────────────────────────
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA query_only=ON;")   # 只读保护
    except Exception as e:
        log.error("数据库连接失败：%s", e)
        return False

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤 0：查询总市场数量（用于漏斗统计）
    # ──────────────────────────────────────────────────────────────────────────
    try:
        total_scanned = conn.execute(
            "SELECT COUNT(*) FROM stock_list"
        ).fetchone()[0]
    except Exception:
        total_scanned = 5000

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤 1：SQL 预筛选（不遍历全市场）
    # ──────────────────────────────────────────────────────────────────────────
    # 先获取 trade_date（数据库最新日期）
    row = conn.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
    trade_date = row[0] if row and row[0] else datetime.now().strftime("%Y%m%d")
    
    _cprint(_BOLD, "[步骤 1] SQL 预筛选（涨幅活跃 + 换手率达标）...")
    prescreened = pre_screen(conn, industry_filter=industry_filter)
    conn.close()   # pre_screen 完成后关闭共享连接

    if not prescreened:
        log.warning("预筛选结果为空：当日无涨幅活跃股票（可能为非交易日或数据未更新）")
        from scripts.feishu_bot import send_text, send_daily_summary
        send_daily_summary([], session_name=session_name, total_scanned=total_scanned, trade_date=trade_date)
        return True   # 非致命

    _cprint(_GREEN, f"  ✅ 预筛选完成：全市场 {total_scanned} 只 → {len(prescreened)} 只活跃股\n")

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤 2：并行打分（ThreadPoolExecutor，CPU × I/O 密集型）
    # ──────────────────────────────────────────────────────────────────────────
    _cprint(_BOLD, f"[步骤 2] 并行打分（{SCORE_WORKERS} 线程）...")
    scored_stocks = score_batch(prescreened, top_n=TOP_N, workers=SCORE_WORKERS)

    if not scored_stocks:
        log.warning("打分后无有效候选股")
        from scripts.feishu_bot import send_daily_summary
        send_daily_summary([], session_name=session_name, total_scanned=total_scanned, trade_date=trade_date)
        return True

    _cprint(_GREEN, f"  ✅ 打分完成：{len(prescreened)} 只 → Top-{len(scored_stocks)} 候选股\n")

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤 3：Ollama 健康检查（仅当有股票需要 AI 分析时执行）
    # ──────────────────────────────────────────────────────────────────────────
    above_threshold = sum(1 for s in scored_stocks if s.get("score", 0) >= AI_TRIGGER_SCORE)

    if above_threshold > 0:
        _cprint(_BOLD, f"[步骤 3] Ollama 健康检查（{above_threshold} 只股票需要 AI 分析）...")
        # exit_on_fail=True：Ollama 未启动时直接 sys.exit(1)，避免空转
        check_ollama(exit_on_fail=True)
        _cprint(_GREEN, "  ✅ Ollama 就绪\n")
    else:
        _cprint(_YELLOW, f"[步骤 3] 无股票达到 AI 门槛（≥{AI_TRIGGER_SCORE}分），跳过 Ollama 检查\n")

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤 4：AI 并发分析
    # ──────────────────────────────────────────────────────────────────────────
    _cprint(_BOLD, f"[步骤 4] AI 深度分析（门槛 ≥{AI_TRIGGER_SCORE}分，并发 {MAX_CONCURRENT}）...")
    ai_results, skipped_count = run_ai_analysis_parallel(
        candidates=scored_stocks,
        market_volume_status=market_volume_status,
        sector_risk=sector_risk,
    )

    push_candidates = [r for r in ai_results if r.get("total_score", 0) >= 80]
    _cprint(_GREEN,
            f"  AI \u5206\u6790\u5b8c\u6210\uff1a{above_threshold} \u53ea\u8fdb\u5165\u5206\u6790 \u2192 "
            f"{len(push_candidates)} \u53ea\u8fbe\u5230\u63a8\u9001\u95e8\u69db\uff08\u226580\u5206\uff09\n")

    # ── v4.0 新增：将精选结果暴露为模块级变量，供 scheduler 步骤4读取 ─────────
    global _last_selected_candidates
    _last_selected_candidates = [
        {
            "ts_code":    r["ts_code"],
            "name":       r.get("name", ""),
            "total_score": r["total_score"],
            "score_card": r.get("score_card", {}),   # 供 trade_plan 计算仓位
        }
        for r in push_candidates
    ]

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤 5：飞书推送
    # ──────────────────────────────────────────────────────────────────────────
    _cprint(_BOLD, "[步骤 5] 飞书交互卡片推送...")
    push_to_feishu(
        ai_results=ai_results,
        filter_results=scored_stocks,
        session_name=session_name,
        total_scanned=total_scanned,
        trade_date=trade_date,
    )
    _cprint(_GREEN, f"  推送完成：{len(push_candidates) + 1} 张卡片\n")

    # ── 总结 ──────────────────────────────────────────────────────────────────
    elapsed_total = time.time() - t_start
    _cprint(_BOLD, "═" * 65)
    _cprint(_GREEN, "  StockAI Funnel v4.0 本次运行完成")
    _cprint(_BOLD, f"  全市场:    {total_scanned:>6,} 只")
    _cprint(_BOLD, f"  预筛选:    {len(prescreened):>6,} 只  (SQL 活跃股过滤)")
    _cprint(_BOLD, f"  Top候选:   {len(scored_stocks):>6,} 只  (并行打分 Top-{TOP_N})")
    _cprint(_BOLD, f"  AI 分析:   {above_threshold:>6,} 只  (>={AI_TRIGGER_SCORE}分进入 Ollama)")
    _cprint(_BOLD, f"  精选推送:  {len(push_candidates):>6,} 只  (>=80分推飞书)")
    _cprint(_BOLD, f"  总耗时:    {elapsed_total:>5.1f} 秒")
    _cprint(_BOLD, "═" * 65 + "\n")

    return True


# 模块级精选结果缓存（由 main() 写入，scheduler 读取）
_last_selected_candidates: List[Dict] = []



# =============================================================================
# CLI 入口
# =============================================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="StockAI Funnel v4.0 三层漏斗主程序")
    parser.add_argument("--session",   default="手动触发",  help="会话名称")
    parser.add_argument("--market",    default="放量",      help="大盘量能: 放量/缩量/平量")
    parser.add_argument("--sector",    default="正常",      help="板块风险: 正常/超配/欠配")
    parser.add_argument("--threshold", type=int, default=None,
                        help=f"覆盖 AI 触发门槛（默认 {AI_TRIGGER_SCORE} 分）")
    parser.add_argument("--pct-min",   type=float, default=PRE_SCREEN_PCT_MIN,
                        help=f"pre_screen 涨幅下限（默认 {PRE_SCREEN_PCT_MIN}%）")
    parser.add_argument("--pct-max",   type=float, default=PRE_SCREEN_PCT_MAX,
                        help=f"pre_screen 涨幅上限（默认 {PRE_SCREEN_PCT_MAX}%）")
    parser.add_argument("--turn-min",  type=float, default=PRE_SCREEN_TURN_MIN,
                        help=f"pre_screen 换手率下限（默认 {PRE_SCREEN_TURN_MIN}%）")
    parser.add_argument("--check-ollama", action="store_true",
                        help="仅执行 Ollama 健康检查，不运行完整流程")
    args = parser.parse_args()

    # 覆盖全局参数
    if args.threshold is not None:
        AI_TRIGGER_SCORE = args.threshold
        log.info("命令行覆盖 AI 触发门槛 → %d 分", AI_TRIGGER_SCORE)

    PRE_SCREEN_PCT_MIN  = args.pct_min
    PRE_SCREEN_PCT_MAX  = args.pct_max
    PRE_SCREEN_TURN_MIN = args.turn_min

    # 仅做健康检查
    if args.check_ollama:
        check_ollama(exit_on_fail=False)
        sys.exit(0)

    ok = main(
        session_name=args.session,
        market_volume_status=args.market,
        sector_risk=args.sector,
    )
    sys.exit(0 if ok else 1)
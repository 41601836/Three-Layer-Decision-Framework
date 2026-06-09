# -*- coding: utf-8 -*-
"""
scheduler.py —— 陈明的专属量化助手 v4.0 定时调度器
=====================================================================
A股交易日四时间点自动扫描 + AI报告 + 飞书推送：
  - 08:40  盘前前瞻（隔夜消息梳理）
  - 11:25  早盘异动（半日资金动向）
  - 14:30  尾盘信号（全天趋势确认）
  - 19:30  盘后深度复盘（次日标的预警）

核心功能：
  1. 持仓体检：读取 portfolio.json，对每只持仓股票进行健康检查
  2. 精选推送：三层漏斗选股，生成精简版"今日操作简报"
  3. 盘中告警：监控止损触发、跌停、主力异常流出

用法：
  python scripts/scheduler.py          # 持续运行，自动按时触发
  python scripts/scheduler.py --now    # 立即执行一次（不等时间点，调试用）
  python scripts/scheduler.py --test   # 仅测试飞书连接
"""

import io
import os
import sys
import time
import json
import logging
import argparse
import pandas as pd
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

# ── 强制 UTF-8 输出（Windows GBK 控制台）──────────────────────────────────────
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

LOG_DIR = os.path.join(ROOT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(
            os.path.join(LOG_DIR, "scheduler_{}.log".format(
                datetime.now().strftime("%Y%m%d"))),
            encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)

# ── 调度时间点（从配置文件读取）─────────────────────────────────────────────────
try:
    from config_loader import get_config
    
    SCHEDULE_TIMES = get_config("scheduler.scan_times", ["08:40", "11:25", "14:30", "20:00"])
    MAX_CANDIDATES = get_config("scheduler.max_candidates", 50)
    BATCH_SIZE = get_config("scheduler.batch_size", 50)
    
except ImportError:
    # 降级方案：使用硬编码默认值
    SCHEDULE_TIMES = ["08:40", "11:25", "14:30", "20:00"]
    MAX_CANDIDATES = 50
    BATCH_SIZE = 50

SESSION_NAMES = {
    "08:40": "盘前前瞻",
    "11:25": "早盘异动",
    "14:30": "尾盘信号",
    "20:00": "盘后复盘",
}

# 区分盘中时段和盘后时段
INTRADAY_SESSIONS = {"盘前前瞻", "早盘异动", "尾盘信号"}
AFTER_HOURS_SESSION = "盘后复盘"


# =============================================================================
# 核心任务
# =============================================================================
def load_portfolio() -> list:
    """读取 portfolio.json 持仓配置文件"""
    portfolio_path = os.path.join(ROOT_DIR, "portfolio.json")
    try:
        with open(portfolio_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log.warning("portfolio.json 不存在，创建空文件")
        with open(portfolio_path, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2)
        return []
    except Exception as e:
        log.error("读取 portfolio.json 失败: %s", e)
        return []


def analyze_portfolio(portfolio: list) -> list:
    """对持仓股票进行体检分析，返回体检结果列表"""
    if not portfolio:
        return []
    
    results = []
    try:
        from analyze_stock import StockAnalyzer
        
        for pos in portfolio:
            ts_code = pos.get("ts_code", "")
            cost = pos.get("cost", 0)
            if not ts_code:
                continue
            
            try:
                analyzer = StockAnalyzer(ts_code)
                # 使用 analyze_v3_0 分析
                total_score, python_score, ai_score, grade, report = analyzer.analyze_v3_0(
                    ts_code=ts_code,
                    catalyst_score=0,
                    industry_mode="normal"
                )
                
                # 生成操作建议
                action, reason, stop_loss = generate_position_action(
                    ts_code, total_score, grade, report, cost
                )
                
                results.append({
                    "ts_code": ts_code,
                    "name": analyzer.get_stock_name(),
                    "cost": cost,
                    "score": total_score,
                    "grade": grade,
                    "action": action,
                    "reason": reason,
                    "stop_loss": stop_loss,
                })
                log.info(f"持仓体检 {ts_code}: {action} (得分: {total_score})")
                
            except Exception as e:
                log.warning(f"持仓体检失败 {ts_code}: {e}")
                continue
                
    except Exception as e:
        log.error("持仓体检模块加载失败: %s", e)
    
    return results


def generate_position_action(ts_code: str, score: int, grade: str, report: str, cost: float) -> tuple:
    """根据分析结果生成持仓操作建议"""
    # 简化的操作建议逻辑
    if score >= 80:
        action = "继续持有"
        reason = "基本面和技术面表现良好"
        # 从报告中提取止损建议
        stop_loss = extract_stop_loss_from_report(report)
        if stop_loss:
            stop_loss = f"止损上移至 {stop_loss}"
        else:
            stop_loss = ""
    elif score >= 60:
        action = "持有观察"
        reason = "表现一般，需关注后续走势"
        stop_loss = ""
    else:
        action = "建议减仓"
        reason = "评分较低，风险较高"
        stop_loss = ""
    
    return action, reason, stop_loss


def extract_stop_loss_from_report(report: str) -> str:
    """从AI报告中提取止损价"""
    import re
    m = re.search(r"止损[：:]?\s*¥?\s*([\d\.]+)", report)
    if m:
        return "¥" + m.group(1)
    return ""


def run_full_pipeline(session_name: str = "手动触发"):
    """
    执行一次完整扫描 → AI报告 → 飞书推送流程（陈明专属精简版）。
    整合功能：
      1. market_env    → 大盘环境判断
      2. industry_strength → 行业强度计算
      3. 持仓体检      → 读取 portfolio.json 进行健康检查
      4. 三层漏斗      → 选股扫描
      5. 精简推送      → 发送"今日操作简报"
    """
    from scripts.scanner import is_trade_day
    from scripts.feishu_bot import send_text, send_daily_brief

    today = datetime.now().strftime("%Y%m%d")

    # ── 非交易日判断（盘后复盘仍执行） ────────────────────────────────────────
    if session_name != "盘后复盘" and not is_trade_day(today):
        log.info("今日（%s）非交易日，跳过本次扫描", today)
        return

    log.info("=" * 60)
    log.info("  陈明的专属量化助手 v4.0 — %s", session_name)
    log.info("  时间：%s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 60)

    # 判断是否为盘中时段
    is_intraday = session_name in INTRADAY_SESSIONS
    
    # 确定数据截止日期
    if is_intraday or session_name == "手动触发":
        # 盘中时段使用昨日数据
        data_date = (datetime.now() - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        data_label = f"📅 数据基于：{data_date}（昨日收盘）"
    else:
        # 盘后时段使用今日数据（需先更新）
        data_date = datetime.now().strftime("%Y-%m-%d")
        data_label = f"📅 数据基于：{data_date}（今日收盘后）"
    
    log.info("[数据] %s", data_label)

    # =========================================================================
    # 步骤0：盘后时段强制更新数据
    # =========================================================================
    if session_name == AFTER_HOURS_SESSION:
        log.info("[数据] 盘后时段，开始增量更新数据...")
        try:
            # 执行数据更新
            import subprocess
            result = subprocess.run(
                [sys.executable, os.path.join(ROOT_DIR, "scripts", "fetch_daily.py")],
                cwd=ROOT_DIR,
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode == 0:
                log.info("[数据] 日线数据更新成功")
            else:
                log.warning("[数据] 日线数据更新失败: %s", result.stderr)
            
            # 更新北向资金数据
            result_hsgt = subprocess.run(
                [sys.executable, os.path.join(ROOT_DIR, "scripts", "fetch_hsgt.py")],
                cwd=ROOT_DIR,
                capture_output=True,
                text=True,
                timeout=120
            )
            if result_hsgt.returncode == 0:
                log.info("[数据] 北向资金数据更新成功")
            else:
                log.warning("[数据] 北向资金更新失败: %s", result_hsgt.stderr)
                
        except Exception as e:
            log.warning("[数据] 数据更新异常: %s", e)
    elif session_name == "尾盘信号":
        # 尾盘时段可拉取分钟线辅助判断
        log.info("[数据] 尾盘时段，拉取今日分钟线数据...")
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, os.path.join(ROOT_DIR, "scripts", "fetch_daily.py"), "--skip-daily", "--skip-moneyflow", "--skip-holder", "--skip-margin", "--skip-block", "--skip-bak"],
                cwd=ROOT_DIR,
                capture_output=True,
                text=True,
                timeout=120
            )
            if result.returncode == 0:
                log.info("[数据] 分钟线数据更新成功")
            else:
                log.warning("[数据] 分钟线更新失败: %s", result.stderr)
        except Exception as e:
            log.warning("[数据] 分钟线更新异常: %s", e)

    # =========================================================================
    # 步骤1：抓取新闻快讯
    # =========================================================================
    try:
        from scripts.news_fetcher import main as fetch_news
        news_mode = "daily" if session_name in ("盘前前瞻", AFTER_HOURS_SESSION, "手动触发") else "intraday"
        log.info("[新闻] 开始抓取快讯（%s 模式）...", news_mode)
        fetch_news(news_mode)
        log.info("[新闻] 快讯抓取完成")
    except Exception as e:
        log.warning("[新闻] 快讯抓取失败: %s", e)

    # =========================================================================
    # 步骤1：大盘环境判断
    # =========================================================================
    market_mode = "defense"
    max_pos = 0.30
    mode_zh = "防守"
    try:
        from market_env import get_market_mode, load_market_mode, MODE_ZH
        if session_name in ("盘前前瞻", "手动触发"):
            market_mode, max_pos, _ = get_market_mode()
        else:
            market_mode, max_pos, _ = load_market_mode()
        mode_zh = MODE_ZH.get(market_mode, market_mode)
        log.info("[大盘] 模式: %s | 仓位上限: %.0f%%", mode_zh, max_pos * 100)

        # 空仓模式：推送休战通知
        if market_mode == "empty":
            send_daily_brief(
                market_mode="空仓",
                max_position=0.0,
                trade_date=today,
            )
            log.info("空仓模式，今日休战")
            return

    except Exception as e:
        log.warning("大盘环境判断失败，使用防守默认值: %s", e)

    # =========================================================================
    # 步骤2：行业强度计算
    # =========================================================================
    main_line, backup_line = [], []
    try:
        from industry_strength import get_strong_industries, load_strong_industries
        if session_name in ("盘后复盘", "手动触发"):
            main_line, backup_line = get_strong_industries()
        else:
            main_line, backup_line = load_strong_industries()
        log.info("[行业] 主线: %s | 备选: %s", main_line or "[]", backup_line or "[]")
    except Exception as e:
        log.warning("行业强度计算失败: %s", e)

    # =========================================================================
    # 步骤3：持仓体检
    # =========================================================================
    portfolio_status = []
    try:
        portfolio = load_portfolio()
        if portfolio:
            log.info("[持仓] 正在体检 %d 只股票", len(portfolio))
            portfolio_status = analyze_portfolio(portfolio)
            log.info("[持仓] 体检完成，生成 %d 条建议", len(portfolio_status))
        else:
            log.info("[持仓] 无持仓数据")
    except Exception as e:
        log.warning("持仓体检失败: %s", e)

    # =========================================================================
    # 步骤4：三层漏斗选股
    # =========================================================================
    selected_stocks = []
    try:
        import main as main_module
        industry_filter = main_line + backup_line if (main_line or backup_line) else None
        ok = main_module.main(
            session_name=session_name,
            market_volume_status="放量" if market_mode == "attack" else "缩量",
            sector_risk="正常",
            industry_filter=industry_filter,
        )
        
        # 获取精选结果
        candidates = getattr(main_module, "_last_selected_candidates", [])
        for c in candidates[:5]:  # 最多取5只
            ts_code    = c.get("ts_code", "")
            confidence = c.get("ai_confidence", -1)  # -1 表示 AI 未运行

            # AI 信心指数过滤：confidence=-1（未运行）不过滤，<70 则跳过
            if confidence != -1 and confidence < 70:
                log.info("[精选] 跳过 %s：AI信心指数 %d/100（< 70，信号质量不足）",
                         ts_code, confidence)
                continue

            conf_label = f" | AI信心: {confidence}/100" if confidence >= 0 else ""
            selected_stocks.append({
                "ts_code":    ts_code,
                "name":       c.get("name", ""),
                "score":      c.get("total_score", 0),
                "suggestion": f"可轻仓介入{conf_label}",
            })
        log.info("[精选] 生成 %d 只精选股票（AI信心指数≥70 过滤后）", len(selected_stocks))
        
    except Exception as e:
        log.error("三层漏斗任务异常: %s", e)

    # =========================================================================
    # 步骤5：发送精简版"今日操作简报"
    # =========================================================================
    try:
        log.info("[推送] 发送今日操作简报...")
        send_daily_brief(
            market_mode=mode_zh,
            max_position=max_pos,
            main_industries=main_line,
            backup_industries=backup_line,
            portfolio_status=portfolio_status,
            selected_stocks=selected_stocks,
            trade_date=today,
            data_label=data_label,
        )
        log.info("[推送] 今日操作简报发送成功")
    except Exception as e:
        log.error("推送失败: %s", e)

    # =========================================================================
    # 步骤6：持仓健康度分析与独立推送
    # =========================================================================
    try:
        from portfolio_health import check_portfolio
        from scripts.feishu_bot import send_portfolio_report
        
        log.info("[健康度] 开始分析持仓健康度...")
        holdings = check_portfolio("portfolio.json")
        if holdings:
            log.info("[健康度] 分析完成 %d 只股票，推送持仓健康度报告", len(holdings))
            send_portfolio_report(holdings, trade_date=today)
        else:
            log.info("[健康度] 无持仓，发送空仓提示")
            send_portfolio_report([], trade_date=today)
    except Exception as e:
        log.error("[健康度] 持仓健康度分析失败: %s", e)




# =============================================================================
# 调度主循环
# =============================================================================
def _is_time_to_run(target_hhmm: str, tolerance_sec: int = 60) -> bool:
    """判断当前时间是否在目标 HH:MM ± tolerance_sec 范围内。"""
    now = datetime.now()
    th, tm = map(int, target_hhmm.split(":"))
    target = now.replace(hour=th, minute=tm, second=0, microsecond=0)
    diff   = abs((now - target).total_seconds())
    return diff <= tolerance_sec


def main_loop():
    """持续轮询，到达时间点即触发任务（每30秒检查一次）。"""
    log.info("⏰ 调度器启动，监听时间点: %s", " / ".join(SCHEDULE_TIMES))
    fired_today: set = set()

    while True:
        now_hhmm = datetime.now().strftime("%H:%M")
        today    = datetime.now().strftime("%Y%m%d")

        # 每天零点重置触发记录
        if now_hhmm == "00:01":
            fired_today.clear()

        for t in SCHEDULE_TIMES:
            key = "{}_{}".format(today, t)
            if key not in fired_today and _is_time_to_run(t, tolerance_sec=55):
                fired_today.add(key)
                session = SESSION_NAMES.get(t, t)
                log.info("🔔 触发时间点 %s — %s", t, session)
                try:
                    run_full_pipeline(session_name=session)
                except Exception as e:
                    log.error("任务异常: %s", e)
                    # 发送错误通知
                    try:
                        from scripts.feishu_bot import send_error_notification
                        send_error_notification(str(e))
                    except Exception as notify_err:
                        log.error("发送错误通知失败: %s", notify_err)

        time.sleep(30)


# =============================================================================
# CLI 入口
# =============================================================================
def parse_args():
    p = argparse.ArgumentParser(description="StockAI v2.1 定时调度器")
    p.add_argument("--now",  action="store_true", help="立即执行一次（调试）")
    p.add_argument("--test", action="store_true", help="仅测试飞书连接")
    p.add_argument("--session", default="手动触发",
                   help="手动触发时的会话名称（v4.0）")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.test:
        from scripts.feishu_bot import send_text
        ok = send_text("🤖 三层漏斗A股分析 调度器连通性测试 (三层漏斗A股分析) — {}".format(
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        log.info("飞书测试: %s", "✅ 成功" if ok else "❌ 失败")

    elif args.now:
        run_full_pipeline(session_name=args.session)

    else:
        main_loop()

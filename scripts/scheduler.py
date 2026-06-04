# -*- coding: utf-8 -*-
"""
scheduler.py —— StockAI v2.1 定时调度器
=====================================================================
A股交易日四时间点自动扫描 + AI报告 + 飞书推送：
  - 08:40  盘前前瞻（隔夜消息梳理）
  - 11:25  早盘异动（半日资金动向）
  - 14:30  尾盘信号（全天趋势确认）
  - 19:30  盘后深度复盘（次日标的预警）

用法：
  python scripts/scheduler.py          # 持续运行，自动按时触发
  python scripts/scheduler.py --now    # 立即执行一次（不等时间点，调试用）
  python scripts/scheduler.py --test   # 仅测试飞书连接
"""

import io
import os
import sys
import time
import logging
import argparse
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

# ── 调度时间点（HH:MM）─────────────────────────────────────────────────────────
SCHEDULE_TIMES = ["08:40", "11:25", "14:30", "19:30"]

SESSION_NAMES = {
    "08:40": "盘前前瞻",
    "11:25": "早盘异动",
    "14:30": "尾盘信号",
    "19:30": "盘后复盘",
}


# =============================================================================
# 核心任务
# =============================================================================
def run_full_pipeline(session_name: str = "手动触发"):
    """
    执行一次完整扫描 → AI报告 → 飞书推送流程（v3.0）。
    统一调用 main.main()，保持单一责任原则。

    大盘量能根据会话时间自动判断：
        盘前前瞻  → 使用前一日量能状态（默认放量）
        早盘/尾盘 → 实时量能（此处简化为默认值，可接入外部数据源）
        盘后复盘  → 当日量能
    """
    from scripts.scanner import is_trade_day
    from scripts.feishu_bot import send_text

    today = datetime.now().strftime("%Y%m%d")

    # ── 非交易日判断（盘后复盘仍执行） ────────────────────────────────────────
    if session_name != "盘后复盘" and not is_trade_day(today):
        log.info("📅 今日（%s）非交易日，跳过本次扫描", today)
        return

    # ── 根据会话名称设置大盘参数 ───────────────────────────────────────────────
    market_map = {
        "盘前前瞻": "放量",
        "早盘异动": "放量",
        "尾盘信号": "放量",
        "盘后复盘": "放量",
    }
    market_status = market_map.get(session_name, "放量")

    log.info("=" * 60)
    log.info("  StockAI Funnel v3.0 调度触发 — %s", session_name)
    log.info("  时间：%s | 大盘量能：%s",
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), market_status)
    log.info("=" * 60)

    try:
        # 调用 main.py 的完整三层漏斗流程
        import main as main_module
        ok = main_module.main(
            session_name=session_name,
            market_volume_status=market_status,
            sector_risk="正常",
        )
        if not ok:
            log.warning("三层漏斗流程返回失败状态（%s）", session_name)
    except Exception as e:
        log.error("任务异常（%s）：%s", session_name, e, exc_info=True)
        send_text(f"⚠️ StockAI Funnel {session_name} 任务异常：{str(e)[:200]}")


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

        time.sleep(30)


# =============================================================================
# CLI 入口
# =============================================================================
def parse_args():
    p = argparse.ArgumentParser(description="StockAI v2.1 定时调度器")
    p.add_argument("--now",  action="store_true", help="立即执行一次（调试）")
    p.add_argument("--test", action="store_true", help="仅测试飞书连接")
    p.add_argument("--session", default="手动触发",
                   help="手动触发时的会话名称")
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

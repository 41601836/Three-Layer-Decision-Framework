import os
import sys
import logging
import psutil
import subprocess
import sqlite3
import re
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import json
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(ROOT_DIR, "scripts")
STATIC_DIR = os.path.join(ROOT_DIR, "static")
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")
TOKENS_PATH = os.path.join(ROOT_DIR, "tokens.py")

# 导入推送历史管理模块
sys.path.append(os.path.join(ROOT_DIR, "scripts"))
from push_history import push_history_manager

os.makedirs(STATIC_DIR, exist_ok=True)

app = FastAPI()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def read_root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

class ConfigUpdate(BaseModel):
    tushare_token: str
    feishu_webhook: str

@app.get("/api/config")
def get_config():
    tushare_token = ""
    feishu_webhook = ""
    if os.path.exists(TOKENS_PATH):
        with open(TOKENS_PATH, "r", encoding="utf-8") as f:
            content = f.read()
            m1 = re.search(r'TOKEN\s*=\s*[\'"]([^\'"]*)[\'"]', content)
            if m1: tushare_token = m1.group(1)
            m2 = re.search(r'FEISHU_WEBHOOK\s*=\s*[\'"]([^\'"]*)[\'"]', content)
            if m2: feishu_webhook = m2.group(1)
    return {"tushare_token": tushare_token, "feishu_webhook": feishu_webhook}

@app.post("/api/config")
def set_config(config: ConfigUpdate):
    content = f"""# tokens.py —— 全局密钥配置（禁止硬编码到其他文件）
# ⚠️ 切勿命名为 token.py！避免与 Python 标准库冲突

# Tushare API Token
TOKEN = "{config.tushare_token}"

# 飞书机器人 Webhook URL
FEISHU_WEBHOOK = "{config.feishu_webhook}"
"""
    with open(TOKENS_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    return {"status": "success"}

def get_process(script_name):
    # Only return processes that are running Python scripts matching script_name
    # Exclude those with '--now' or '--test' if we only want the background service
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info['cmdline']
            if cmdline and "python" in proc.info['name'].lower() and any(script_name in cmd for cmd in cmdline):
                # For scheduler, we only consider it the background service if it lacks --now / --test
                if script_name == "scheduler.py":
                    if "--now" in cmdline or "--test" in cmdline:
                        continue
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return None

@app.get("/api/status")
def get_status():
    scheduler_running = get_process("scheduler.py") is not None
    fetcher_running = get_process("fetch_daily.py") is not None
    
    db_stats = {"stock_count": 0, "daily_count": 0, "moneyflow_count": 0, "holder_count": 0}
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            db_stats["stock_count"] = conn.execute("SELECT COUNT(*) FROM stock_list").fetchone()[0]
            db_stats["daily_count"] = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
            db_stats["moneyflow_count"] = conn.execute("SELECT COUNT(*) FROM moneyflow").fetchone()[0]
            db_stats["holder_count"] = conn.execute("SELECT COUNT(*) FROM stk_holdernumber").fetchone()[0]
            conn.close()
        except:
            pass
            
    return {
        "scheduler_running": scheduler_running,
        "fetcher_running": fetcher_running,
        "db": db_stats
    }

class FetchCmd(BaseModel):
    workers: int = 3
    mode: str = "incremental" # "incremental" or "full"

@app.post("/api/fetch")
def run_fetch(cmd: FetchCmd):
    if get_process("fetch_daily.py"):
        raise HTTPException(status_code=400, detail="Data fetch already running")
    command = [sys.executable, os.path.join(SCRIPTS_DIR, "fetch_daily.py"), "--workers", str(cmd.workers)]
    if cmd.mode == "full":
        command.append("--start")
        command.append("20200101")
    subprocess.Popen(command, cwd=ROOT_DIR)
    return {"status": "started"}

class ScanCmd(BaseModel):
    session_name: str = "手动触发"

@app.post("/api/scan")
def run_scan(cmd: ScanCmd):
    command = [sys.executable, os.path.join(SCRIPTS_DIR, "scheduler.py"), "--now", "--session", cmd.session_name]
    subprocess.Popen(command, cwd=ROOT_DIR)
    return {"status": "started"}

class SchedulerCmd(BaseModel):
    action: str # "start" or "stop"

@app.post("/api/scheduler")
def toggle_scheduler(cmd: SchedulerCmd):
    proc = get_process("scheduler.py")
    if cmd.action == "stop":
        if proc:
            proc.terminate()
        return {"status": "stopped"}
    elif cmd.action == "start":
        if not proc:
            command = [sys.executable, os.path.join(SCRIPTS_DIR, "scheduler.py")]
            subprocess.Popen(command, cwd=ROOT_DIR)
        return {"status": "started"}
        
@app.post("/api/test_feishu")
def test_feishu():
    try:
        from scripts.feishu_bot import send_text
        send_text("🔔 StockAI v4.0 测试消息：您的 Webhook 配置正确，飞书通道连通性正常！")
        return {"status": "success", "message": "Test message sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/logs")
def get_logs():
    log_dir = os.path.join(ROOT_DIR, "logs")
    if not os.path.exists(log_dir):
        return {"logs": "日志目录不存在。"}
    
    # 获取最新的 .log 文件
    log_files = [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith(".log")]
    if not log_files:
        return {"logs": "暂无日志。"}
    
    latest_log = max(log_files, key=os.path.getmtime)
    try:
        with open(latest_log, "r", encoding="utf-8") as f:
            # 读取最后 50 行
            lines = f.readlines()[-50:]
            return {"logs": "".join(lines)}
    except Exception as e:
        return {"logs": f"无法读取日志: {e}"}

# ──────────────────────────────────────────────────────────────
# 洗盘分析 API
# ──────────────────────────────────────────────────────────────

class WashoutRequest(BaseModel):
    ts_code: str

@app.post("/api/washout/analyze")
def analyze_washout(req: WashoutRequest):
    try:
        from washout_analyst import analyze, analyze_and_save
        
        ts_code = req.ts_code.strip()
        if not ts_code:
            raise HTTPException(status_code=400, detail="股票代码不能为空")
        
        report = analyze(ts_code)
        filepath = analyze_and_save(ts_code)
        
        return {
            "status": "success",
            "ts_code": ts_code,
            "report": report,
            "file_path": filepath
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/washout/portfolio")
def get_portfolio_for_washout():
    portfolio_file = os.path.join(ROOT_DIR, "portfolio.json")
    if not os.path.exists(portfolio_file):
        return {"stocks": []}
    try:
        with open(portfolio_file, "r", encoding="utf-8") as f:
            return {"stocks": json.load(f)}
    except Exception as e:
        return {"stocks": []}

import json
AI_CONFIG_PATH = os.path.join(ROOT_DIR, "ai_config.json")

class AIConfigUpdate(BaseModel):
    market_env: str = "震荡"
    max_bias: int = 15
    sector_focus: str = ""

@app.get("/api/ai_config")
def get_ai_config():
    if os.path.exists(AI_CONFIG_PATH):
        try:
            with open(AI_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"market_env": "震荡", "max_bias": 15, "sector_focus": ""}

@app.post("/api/ai_config")
def set_ai_config(config: AIConfigUpdate):
    with open(AI_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config.dict(), f, ensure_ascii=False, indent=2)
    return {"status": "success"}

# ═══════════════════════════════════════════════════
# 推送历史API
# ═══════════════════════════════════════════════════
@app.get("/api/push-history")
def get_push_history(
    page: int = 1,
    page_size: int = 20,
    search: str = None,
    status: str = None,
    grade: str = None,
    start_time: str = None,
    end_time: str = None
):
    """获取推送历史列表"""
    try:
        result = push_history_manager.get_push_history(
            page=page,
            page_size=page_size,
            search=search,
            status=status,
            grade=grade,
            start_time=start_time,
            end_time=end_time
        )
        return {
            "code": 0,
            "message": "success",
            "data": result
        }
    except Exception as e:
        logger.error(f"获取推送历史失败: {e}")
        return {
            "code": -1,
            "message": "获取推送历史失败",
            "data": {}
        }

@app.get("/api/push-history/{record_id}")
def get_push_detail(record_id: int):
    """获取推送详情"""
    try:
        detail = push_history_manager.get_push_detail(record_id)
        if detail:
            return {
                "code": 0,
                "message": "success",
                "data": detail
            }
        else:
            return {
                "code": -1,
                "message": "记录不存在",
                "data": {}
            }
    except Exception as e:
        logger.error(f"获取推送详情失败: {e}")
        return {
            "code": -1,
            "message": "获取推送详情失败",
            "data": {}
        }

@app.post("/api/push-history")
def add_push_history(
    ts_code: str,
    stock_name: str,
    push_reason: str,
    push_content: str = None,
    session_name: str = None,
    total_score: float = None,
    grade: str = None,
    push_status: str = "success"
):
    """添加推送记录"""
    try:
        # 解析JSON内容
        if push_content:
            try:
                push_content = json.loads(push_content)
            except json.JSONDecodeError:
                pass
        
        record_id = push_history_manager.add_push_record(
            ts_code=ts_code,
            stock_name=stock_name,
            push_reason=push_reason,
            push_content=push_content,
            session_name=session_name,
            total_score=total_score,
            grade=grade,
            push_status=push_status
        )
        
        if record_id > 0:
            return {
                "code": 0,
                "message": "success",
                "data": {"record_id": record_id}
            }
        else:
            return {
                "code": -1,
                "message": "保存失败",
                "data": {}
            }
    except Exception as e:
        logger.error(f"添加推送记录失败: {e}")
        return {
            "code": -1,
            "message": "添加推送记录失败",
            "data": {}
        }

@app.get("/api/push-history/stats")
def get_push_stats(days: int = 7):
    """获取推送统计信息"""
    try:
        stats = push_history_manager.get_statistics(days=days)
        
        # 计算今日推送数量
        today = datetime.now().strftime('%Y-%m-%d')
        today_count = push_history_manager.get_push_history(
            page=1,
            page_size=1,
            start_time=f"{today} 00:00:00",
            end_time=f"{today} 23:59:59"
        )['total']
        stats['today_pushes'] = today_count
        
        return {
            "code": 0,
            "message": "success",
            "data": stats
        }
    except Exception as e:
        logger.error(f"获取推送统计失败: {e}")
        return {
            "code": -1,
            "message": "获取推送统计失败",
            "data": {}
        }
    
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080)

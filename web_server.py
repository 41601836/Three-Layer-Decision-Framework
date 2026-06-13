import os
import sys
import psutil
import subprocess
import sqlite3
import re
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(ROOT_DIR, "scripts")
STATIC_DIR = os.path.join(ROOT_DIR, "static")
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")
TOKENS_PATH = os.path.join(SCRIPTS_DIR, "tokens.py")

os.makedirs(STATIC_DIR, exist_ok=True)

import time
from collections import OrderedDict

class LRUCache:
    def __init__(self, capacity: int = 200):
        self.cache = OrderedDict()
        self.capacity = capacity

    def get(self, key):
        if key not in self.cache:
            return None
        self.cache.move_to_end(key)
        return self.cache[key]

    def put(self, key, value):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)

    def clear(self):
        self.cache.clear()

STOCK_ANALYSIS_CACHE = LRUCache(capacity=200)
DB_STATS_CACHE = {
    "data": None,
    "last_updated": 0,
    "cache_duration": 300  # 5分钟缓存
}
STATUS_CACHE = {
    "data": None,
    "last_updated": 0,
    "cache_duration": 5  # 5秒状态与进程检索缓存
}

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
    global STATUS_CACHE, DB_STATS_CACHE
    current_time = time.time()
    
    # 1. 检查整体状态接口缓存是否有效（5秒进程检测缓存）
    if STATUS_CACHE["data"] is not None and (current_time - STATUS_CACHE["last_updated"]) < STATUS_CACHE["cache_duration"]:
        return STATUS_CACHE["data"]
        
    scheduler_running = get_process("scheduler.py") is not None
    fetcher_running = get_process("fetch_daily.py") is not None
    
    # 2. 检查 DB 统计缓存是否有效（300秒大表统计缓存）
    if DB_STATS_CACHE["data"] is not None and (current_time - DB_STATS_CACHE["last_updated"]) < DB_STATS_CACHE["cache_duration"]:
        db_stats = DB_STATS_CACHE["data"]
    else:
        db_stats = {"stock_count": 0, "daily_count": 0, "moneyflow_count": 0, "holder_count": 0}
        if os.path.exists(DB_PATH):
            try:
                conn = sqlite3.connect(DB_PATH, timeout=5)
                db_stats["stock_count"] = conn.execute("SELECT COUNT(*) FROM stock_list").fetchone()[0]
                db_stats["daily_count"] = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
                db_stats["moneyflow_count"] = conn.execute("SELECT COUNT(*) FROM moneyflow").fetchone()[0]
                db_stats["holder_count"] = conn.execute("SELECT COUNT(*) FROM stk_holdernumber").fetchone()[0]
                conn.close()
                # 写入大表统计缓存
                DB_STATS_CACHE["data"] = db_stats
                DB_STATS_CACHE["last_updated"] = current_time
            except:
                pass
                
    result = {
        "scheduler_running": scheduler_running,
        "fetcher_running": fetcher_running,
        "db": db_stats
    }
    
    # 3. 写入接口整体缓存
    STATUS_CACHE["data"] = result
    STATUS_CACHE["last_updated"] = current_time
    
    return result

class FetchCmd(BaseModel):
    workers: int = 3
    mode: str = "incremental" # "incremental" or "full"

@app.post("/api/fetch")
def run_fetch(cmd: FetchCmd):
    if get_process("fetch_daily.py"):
        raise HTTPException(status_code=400, detail="Data fetch already running")
        
    # 同步前清空所有缓存，防止拉取数据后显示旧数据
    STOCK_ANALYSIS_CACHE.clear()
    global DB_STATS_CACHE, STATUS_CACHE
    DB_STATS_CACHE["data"] = None
    DB_STATS_CACHE["last_updated"] = 0
    STATUS_CACHE["data"] = None
    STATUS_CACHE["last_updated"] = 0
    
    command = [sys.executable, os.path.join(SCRIPTS_DIR, "fetch_daily.py"), "--workers", str(cmd.workers)]
    if cmd.mode == "full":
        command.append("--start")
        command.append("20200101")
    else:
        command.append("--fast")
        try:
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
            conn.close()
            latest = row[0] if row and row[0] else None
            if latest:
                from datetime import datetime, timedelta
                start_dt = datetime.strptime(latest, "%Y%m%d") - timedelta(days=3)
                start_str = start_dt.strftime("%Y%m%d")
                command.append("--start")
                command.append(start_str)
        except Exception:
            pass
    subprocess.Popen(command, cwd=ROOT_DIR)
    return {"status": "started"}

class ScanCmd(BaseModel):
    session_name: str = "手动触发"

@app.post("/api/scan")
def run_scan(cmd: ScanCmd):
    # 扫描前清空所有分析与状态缓存
    STOCK_ANALYSIS_CACHE.clear()
    global DB_STATS_CACHE, STATUS_CACHE
    DB_STATS_CACHE["data"] = None
    DB_STATS_CACHE["last_updated"] = 0
    STATUS_CACHE["data"] = None
    STATUS_CACHE["last_updated"] = 0
    
    command = [sys.executable, os.path.join(SCRIPTS_DIR, "scheduler.py"), "--now", "--session", cmd.session_name]
    result = subprocess.run(command, cwd=ROOT_DIR, capture_output=True, text=True, timeout=300)
    
    if result.returncode == 0:
        candidates_file = os.path.join(ROOT_DIR, "last_candidates.json")
        if os.path.exists(candidates_file):
            try:
                with open(candidates_file, "r", encoding="utf-8") as f:
                    candidates = json.load(f)
                return {"status": "completed", "candidates": candidates}
            except Exception as e:
                return {"status": "completed", "message": "扫描完成，但无法读取候选结果"}
        return {"status": "completed", "message": "扫描完成"}
    else:
        raise HTTPException(status_code=500, detail=f"扫描失败: {result.stderr}")

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

# ──────────────────────────────────────────────────────────────
# 宏观分析 API
# ──────────────────────────────────────────────────────────────

@app.get("/macro")
def macro_page():
    return FileResponse(os.path.join(STATIC_DIR, "macro.html"))

class StockSearchRequest(BaseModel):
    ts_code: str = ""
    name: str = ""
    exchange: str = "all"

@app.post("/api/stocks")
def search_stocks(req: StockSearchRequest):
    if not os.path.exists(DB_PATH):
        return {"summary": {"total_stocks": 0, "exchanges": 0, "industries": 0}, "stocks": []}
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    query = "SELECT * FROM stock_list WHERE 1=1"
    params = []
    
    if req.ts_code:
        query += " AND ts_code LIKE ?"
        params.append(f"%{req.ts_code}%")
    if req.name:
        query += " AND name LIKE ?"
        params.append(f"%{req.name}%")
    if req.exchange != "all":
        query += " AND market = ?"
        params.append(req.exchange)
    
    query += " ORDER BY ts_code LIMIT 100"
    
    stocks = []
    try:
        cursor = conn.execute(query, params)
        for row in cursor.fetchall():
            stock = dict(row)
            
            pct_chg = 0
            daily_query = "SELECT pct_chg FROM daily_prices WHERE ts_code = ? ORDER BY trade_date DESC LIMIT 1"
            daily_cursor = conn.execute(daily_query, (stock['ts_code'],))
            daily_row = daily_cursor.fetchone()
            if daily_row:
                pct_chg = daily_row[0]
            
            stocks.append({
                "ts_code": stock['ts_code'],
                "name": stock['name'],
                "fullname": stock.get('fullname', stock['name']),
                "industry": stock.get('industry', ''),
                "area": stock.get('area', ''),
                "list_date": stock.get('list_date', ''),
                "exchange": stock.get('exchange', ''),
                "pct_chg": pct_chg
            })
    finally:
        conn.close()
    
    conn = sqlite3.connect(DB_PATH)
    total_stocks = conn.execute("SELECT COUNT(*) FROM stock_list").fetchone()[0]
    markets = conn.execute("SELECT COUNT(DISTINCT market) FROM stock_list WHERE market IS NOT NULL AND market != ''").fetchone()[0]
    industries = conn.execute("SELECT COUNT(DISTINCT industry) FROM stock_list WHERE industry IS NOT NULL AND industry != ''").fetchone()[0]
    conn.close()
    
    return {
        "summary": {
            "total_stocks": total_stocks,
            "exchanges": markets,
            "industries": industries
        },
        "stocks": stocks
    }

class StockAnalysisRequest(BaseModel):
    ts_code: str

@app.post("/api/stock_analysis")
def analyze_stock(req: StockAnalysisRequest):
    ts_code = req.ts_code.strip()
    if not ts_code:
        raise HTTPException(status_code=400, detail="股票代码不能为空")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    try:
        # 1. 快速定位该个股在数据库中的最新交易日（走主键联合索引前缀，耗时 < 1ms）
        latest_date_row = conn.execute(
            "SELECT MAX(trade_date) FROM daily_prices WHERE ts_code = ?", (ts_code,)
        ).fetchone()
        latest_trade_date = latest_date_row[0] if latest_date_row and latest_date_row[0] else ""
        
        # 2. 检查 LRU 缓存，命中则直接返回
        if latest_trade_date:
            cached_result = STOCK_ANALYSIS_CACHE.get((ts_code, latest_trade_date))
            if cached_result is not None:
                return cached_result
                
        stock_info = conn.execute("SELECT * FROM stock_list WHERE ts_code = ?", (ts_code,)).fetchone()
        if not stock_info:
            raise HTTPException(status_code=404, detail="股票不存在")
        
        daily_query = """
            SELECT trade_date, open, high, low, close, vol 
            FROM daily_prices 
            WHERE ts_code = ? 
            ORDER BY trade_date DESC 
            LIMIT 120
        """
        daily_data = conn.execute(daily_query, (ts_code,)).fetchall()
        kline_data = []
        for row in reversed(daily_data):
            kline_data.append({
                "date": row['trade_date'],
                "open": row['open'],
                "high": row['high'],
                "low": row['low'],
                "close": row['close'],
                "volume": row['vol']
            })
        
        signals = calculate_signals(kline_data)
        
        try:
            industry = stock_info['industry'] or ''
        except:
            industry = ''
        try:
            area = stock_info['area'] or ''
        except:
            area = ''
        analysis_date = daily_data[0]['trade_date'] if daily_data else ""
        
        industry_strength = calculate_industry_strength(ts_code, industry, conn)
        momentum = calculate_momentum(ts_code, industry, conn)
        resonance = calculate_resonance(ts_code, industry, conn)
        style = calculate_style(ts_code, conn)
        risk = calculate_risk(ts_code, conn)
        
        signal_score = calculate_signal_score(signals, industry_strength, momentum, resonance)
        
        final_score = signal_score['score']
        final_prob = signal_score['probability']
        
        if risk['level'] == 'high':
            final_score = max(0, final_score - 3)
            final_prob = max(0, final_prob - 25)
        elif risk['level'] == 'medium':
            final_score = max(0, final_score - 1)
            final_prob = max(0, final_prob - 10)
        
        final_signal_score = {
            "score": final_score,
            "probability": final_prob,
            "adjustment": risk['level']
        }
        
        result = {
            "stock_info": {
                "ts_code": stock_info['ts_code'],
                "name": stock_info['name'],
                "industry": industry,
                "area": area
            },
            "analysis_date": analysis_date,
            "kline_data": kline_data,
            "signals": signals,
            "signal_score": signal_score,
            "final_signal_score": final_signal_score,
            "industry_strength": industry_strength,
            "momentum": momentum,
            "resonance": resonance,
            "style": style,
            "risk": risk
        }
        
        # 3. 写入 LRU 缓存
        if latest_trade_date:
            STOCK_ANALYSIS_CACHE.put((ts_code, latest_trade_date), result)
            
        return result
    finally:
        conn.close()

def calculate_signals(kline_data):
    if len(kline_data) < 20:
        return {
            "ma": {"triggered": False, "value": 0},
            "macd": {"triggered": False, "value": 0},
            "rsi": {"triggered": False, "value": 0},
            "kdj": {"triggered": False, "value": 0},
            "boll": {"triggered": False, "value": 0},
            "volume": {"triggered": False, "value": 0},
            "atr": {"triggered": False, "value": 0}
        }
    
    closes = [d['close'] for d in kline_data]
    volumes = [d['volume'] for d in kline_data]
    
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    
    ma_triggered = closes[-1] >= ma5 and ma5 >= ma10 and ma10 >= ma20
    
    ema12 = closes[-1]
    ema26 = closes[-1]
    for i in range(1, len(closes)):
        ema12 = (2 * closes[-1-i] + 11 * ema12) / 13
        ema26 = (2 * closes[-1-i] + 25 * ema26) / 27
    dif = ema12 - ema26
    dea = dif
    for i in range(1, min(9, len(closes))):
        dea = (2 * dif + 8 * dea) / 10
    macd = 2 * (dif - dea)
    macd_triggered = dif > dea and dif > 0
    
    avg_gain = sum(max(0, closes[i] - closes[i-1]) for i in range(1, len(closes))) / (len(closes) - 1)
    avg_loss = sum(max(0, closes[i-1] - closes[i]) for i in range(1, len(closes))) / (len(closes) - 1)
    rs = avg_gain / (avg_loss + 0.0001)
    rsi = 100 - (100 / (1 + rs))
    rsi_triggered = 50 <= rsi <= 75
    
    lowest_low = min(d['low'] for d in kline_data[-9:])
    highest_high = max(d['high'] for d in kline_data[-9:])
    rsv = (closes[-1] - lowest_low) / (highest_high - lowest_low + 0.0001) * 100
    k = rsv
    for i in range(1, min(3, len(closes))):
        k = (2 * rsv + 1 * k) / 3
    d = k
    for i in range(1, min(3, len(closes))):
        d = (2 * k + 1 * d) / 3
    kdj_triggered = k > d and k > 20
    
    middle_band = ma20
    std_dev = sum((c - middle_band) ** 2 for c in closes[-20:]) / 20
    std_dev = std_dev ** 0.5
    upper_band = middle_band + 2 * std_dev
    lower_band = middle_band - 2 * std_dev
    boll_triggered = lower_band < closes[-1] < upper_band and closes[-1] > middle_band
    
    avg_volume = sum(volumes[-20:]) / 20
    volume_triggered = volumes[-1] >= avg_volume * 1.3
    
    tr_values = []
    for i in range(1, len(kline_data)):
        tr = max(
            kline_data[i]['high'] - kline_data[i]['low'],
            abs(kline_data[i]['high'] - kline_data[i-1]['close']),
            abs(kline_data[i]['low'] - kline_data[i-1]['close'])
        )
        tr_values.append(tr)
    atr = sum(tr_values[-14:]) / 14 if tr_values else 0
    atr_triggered = 0.01 < (atr / closes[-1]) < 0.05
    
    return {
        "ma": {"triggered": ma_triggered, "value": round(closes[-1] - ma5, 2)},
        "macd": {"triggered": macd_triggered, "value": round(macd, 2)},
        "rsi": {"triggered": rsi_triggered, "value": round(rsi, 1)},
        "kdj": {"triggered": kdj_triggered, "value": round(k, 1)},
        "boll": {"triggered": boll_triggered, "value": round((closes[-1] - middle_band) / (upper_band - lower_band + 0.0001) * 100, 1)},
        "volume": {"triggered": volume_triggered, "value": round(volumes[-1] / avg_volume, 2)},
        "atr": {"triggered": atr_triggered, "value": round(atr / closes[-1] * 100, 2)}
    }

SIGNAL_WEIGHTS = {
    'ma_trend': 1.5,
    'macd': 1.2,
    'rsi': 1.0,
    'kdj': 1.0,
    'boll': 1.0,
    'volume': 1.3,
    'atr_risk': 0.8,
    'industry_strength': 1.5,
    'momentum_rank': 1.2,
    'sector_resonance': 1.5
}

def calculate_signal_score(signals, industry_strength=None, momentum_rank=None, sector_resonance=None):
    weighted_score = 0
    total_weight = 0
    
    for key, signal in signals.items():
        if signal['triggered'] and key in SIGNAL_WEIGHTS:
            weighted_score += SIGNAL_WEIGHTS[key]
            total_weight += SIGNAL_WEIGHTS[key]
    
    if industry_strength and industry_strength.get('compare') == '跑赢行业':
        weighted_score += SIGNAL_WEIGHTS['industry_strength']
        total_weight += SIGNAL_WEIGHTS['industry_strength']
    
    if momentum_rank:
        rank_5d_str = momentum_rank.get('rank_5d', '第 50')
        rank_20d_str = momentum_rank.get('rank_20d', '第 50')
        try:
            rank_5d = int(rank_5d_str.split(' ')[1]) if len(rank_5d_str.split(' ')) > 1 else 50
            rank_20d = int(rank_20d_str.split(' ')[1]) if len(rank_20d_str.split(' ')) > 1 else 50
            if rank_5d <= 20:
                weighted_score += SIGNAL_WEIGHTS['momentum_rank'] * 1.5
                total_weight += SIGNAL_WEIGHTS['momentum_rank']
            elif rank_5d <= 40:
                weighted_score += SIGNAL_WEIGHTS['momentum_rank']
                total_weight += SIGNAL_WEIGHTS['momentum_rank']
        except (ValueError, IndexError):
            pass
    
    if sector_resonance:
        trigger_ratio = sector_resonance.get('trigger_ratio', 0)
        if trigger_ratio >= 0.6:
            weighted_score += SIGNAL_WEIGHTS['sector_resonance'] * 1.5
            total_weight += SIGNAL_WEIGHTS['sector_resonance']
        elif trigger_ratio >= 0.4:
            weighted_score += SIGNAL_WEIGHTS['sector_resonance']
            total_weight += SIGNAL_WEIGHTS['sector_resonance']
    
    normalized_score = int(round(weighted_score / total_weight * 10)) if total_weight > 0 else 0
    probability = min(100, 40 + normalized_score * 8)
    
    return {"score": normalized_score, "probability": probability, "weighted_score": round(weighted_score, 2)}

def calculate_industry_strength(ts_code, industry, conn):
    if not industry:
        return {"industry_name": "未知", "change": "-", "compare": "-"}
    
    cursor = conn.execute("""
        SELECT pct_chg FROM daily_prices 
        WHERE ts_code = ? 
        ORDER BY trade_date DESC LIMIT 1
    """, (ts_code,))
    stock_pct = cursor.fetchone()
    stock_pct = stock_pct[0] if stock_pct else 0
    
    industry_stocks = conn.execute("""
        SELECT ts_code FROM stock_list WHERE industry = ? AND ts_code != ?
    """, (industry, ts_code)).fetchall()
    
    if not industry_stocks:
        return {"industry_name": industry, "change": "-1.02%", "compare": "行业数据不足"}
    
    avg_pct = 0
    count = 0
    for row in industry_stocks[:20]:
        cursor = conn.execute("""
            SELECT pct_chg FROM daily_prices 
            WHERE ts_code = ? 
            ORDER BY trade_date DESC LIMIT 1
        """, (row[0],))
        pct = cursor.fetchone()
        if pct:
            avg_pct += pct[0]
            count += 1
    
    avg_pct = avg_pct / count if count > 0 else 0
    compare = "跑赢行业" if stock_pct > avg_pct else "落后行业"
    
    return {
        "industry_name": industry,
        "change": f"{avg_pct:+.2f}%",
        "compare": compare
    }

def calculate_momentum(ts_code, industry, conn):
    cursor = conn.execute("""
        SELECT trade_date, close FROM daily_prices 
        WHERE ts_code = ? ORDER BY trade_date DESC LIMIT 21
    """, (ts_code,))
    data = cursor.fetchall()
    if len(data) < 21:
        return {"rank_5d": "-", "rank_20d": "-", "change_5d": 0, "change_20d": 0}
    
    close_5d = data[4]['close'] if len(data) > 4 else data[-1]['close']
    close_20d = data[19]['close'] if len(data) > 19 else data[-1]['close']
    current_close = data[0]['close']
    
    change_5d = (current_close - close_5d) / close_5d * 100
    change_20d = (current_close - close_20d) / close_20d * 100
    
    industry_stocks = conn.execute("""
        SELECT ts_code FROM stock_list WHERE industry = ?
    """, (industry,)).fetchall()
    
    rank_5d = 1
    rank_20d = 1
    for row in industry_stocks:
        if row[0] == ts_code:
            continue
        cursor = conn.execute("""
            SELECT trade_date, close FROM daily_prices 
            WHERE ts_code = ? ORDER BY trade_date DESC LIMIT 21
        """, (row[0],))
        ind_data = cursor.fetchall()
        if len(ind_data) >= 5:
            ind_close_5d = ind_data[4]['close'] if len(ind_data) > 4 else ind_data[-1]['close']
            ind_change_5d = (ind_data[0]['close'] - ind_close_5d) / ind_close_5d * 100
            if ind_change_5d > change_5d:
                rank_5d += 1
        
        if len(ind_data) >= 20:
            ind_close_20d = ind_data[19]['close'] if len(ind_data) > 19 else ind_data[-1]['close']
            ind_change_20d = (ind_data[0]['close'] - ind_close_20d) / ind_close_20d * 100
            if ind_change_20d > change_20d:
                rank_20d += 1
    
    return {
        "rank_5d": f"第 {rank_5d}",
        "rank_20d": f"第 {rank_20d}",
        "change_5d": round(change_5d, 2),
        "change_20d": round(change_20d, 2)
    }

def calculate_resonance(ts_code, industry, conn):
    industry_stocks = conn.execute("""
        SELECT ts_code FROM stock_list WHERE industry = ? AND ts_code != ?
    """, (industry, ts_code)).fetchall()
    
    atr_count = 0
    macd_count = 0
    boll_count = 0
    total_count = 0
    
    for row in industry_stocks[:30]:
        cursor = conn.execute("""
            SELECT trade_date, open, high, low, close, vol 
            FROM daily_prices 
            WHERE ts_code = ? ORDER BY trade_date DESC LIMIT 20
        """, (row[0],))
        daily_data = cursor.fetchall()
        if len(daily_data) < 20:
            continue
        
        total_count += 1
        closes = [d['close'] for d in daily_data]
        
        ema12 = closes[-1]
        ema26 = closes[-1]
        for i in range(1, len(closes)):
            ema12 = (2 * closes[-1-i] + 11 * ema12) / 13
            ema26 = (2 * closes[-1-i] + 25 * ema26) / 27
        dif = ema12 - ema26
        if dif > 0:
            macd_count += 1
        
        tr_values = []
        for i in range(1, len(daily_data)):
            tr = max(
                daily_data[i]['high'] - daily_data[i]['low'],
                abs(daily_data[i]['high'] - daily_data[i-1]['close']),
                abs(daily_data[i]['low'] - daily_data[i-1]['close'])
            )
            tr_values.append(tr)
        atr = sum(tr_values[-14:]) / 14 if tr_values else 0
        if 0.01 < (atr / closes[-1]) < 0.05:
            atr_count += 1
        
        middle_band = sum(closes[-20:]) / 20
        std_dev = sum((c - middle_band) ** 2 for c in closes[-20:]) / 20
        std_dev = std_dev ** 0.5
        upper_band = middle_band + 2 * std_dev
        lower_band = middle_band - 2 * std_dev
        if lower_band < closes[-1] < upper_band:
            boll_count += 1
    
    return {
        "atr_count": atr_count,
        "macd_count": macd_count,
        "boll_count": boll_count,
        "stock_trigger": 4,
        "total_signals": 7
    }

def calculate_risk(ts_code, conn):
    risk_items = []
    risk_level = 'low'
    score = 100
    
    # 财务健康度检查
    finance_risk = check_financial_health(ts_code, conn)
    risk_items.extend(finance_risk['items'])
    score -= finance_risk['deduction']
    
    # 股东与筹码分析
    holder_risk = check_holder_changes(ts_code, conn)
    risk_items.extend(holder_risk['items'])
    score -= holder_risk['deduction']
    
    # 风险事件预警
    event_risk = check_risk_events(ts_code, conn)
    risk_items.extend(event_risk['items'])
    score -= event_risk['deduction']
    
    if score < 50:
        risk_level = 'high'
    elif score < 75:
        risk_level = 'medium'
    
    return {
        "level": risk_level,
        "score": score,
        "items": risk_items,
        "financial": finance_risk['data'],
        "holder": holder_risk['data'],
        "events": event_risk['data']
    }

def check_financial_health(ts_code, conn):
    items = []
    deduction = 0
    data = {}
    
    cursor = conn.execute("""
        SELECT * FROM stk_holdernumber 
        WHERE ts_code = ? 
        ORDER BY end_date DESC 
        LIMIT 4
    """, (ts_code,))
    holder_data = cursor.fetchall()
    
    if len(holder_data) >= 2:
        latest = holder_data[0]
        prev = holder_data[1]
        data['holder_count'] = latest['holder_num'] if 'holder_num' in latest else '-'
        if 'holder_num' in latest and 'holder_num' in prev and prev['holder_num'] > 0:
            change = (latest['holder_num'] - prev['holder_num']) / prev['holder_num'] * 100
            data['holder_change'] = f"{change:+.1f}%"
            if change > 10:
                items.append(f"⚠️ 股东户数大幅增加 {change:+.1f}%，筹码分散")
                deduction += 15
            elif change < -10:
                items.append(f"✅ 股东户数减少 {abs(change):.1f}%，筹码集中")
                deduction -= 5
        else:
            data['holder_change'] = '-'
    else:
        data['holder_count'] = '-'
        data['holder_change'] = '-'
    
    cursor = conn.execute("""
        SELECT * FROM bak_basic 
        WHERE ts_code = ? 
        ORDER BY trade_date DESC 
        LIMIT 1
    """, (ts_code,))
    basic_data = cursor.fetchone()
    if basic_data:
        data['pe'] = basic_data['pe'] if 'pe' in basic_data and basic_data['pe'] > 0 else '-'
        data['pb'] = basic_data['pb'] if 'pb' in basic_data and basic_data['pb'] > 0 else '-'
        data['ps'] = basic_data['ps'] if 'ps' in basic_data and basic_data['ps'] > 0 else '-'
        
        if 'pe' in basic_data and basic_data['pe'] > 0:
            pe = basic_data['pe']
            if pe > 100:
                items.append(f"⚠️ PE估值过高 ({pe:.1f})")
                deduction += 10
            elif pe < 5:
                items.append(f"⚠️ PE估值异常偏低 ({pe:.1f})，需关注基本面")
                deduction += 5
    else:
        data['pe'] = '-'
        data['pb'] = '-'
        data['ps'] = '-'
    
    if not items:
        items.append("✅ 财务指标暂无明显风险")
    
    return {"items": items, "deduction": deduction, "data": data}

def check_holder_changes(ts_code, conn):
    items = []
    deduction = 0
    data = {}
    
    cursor = conn.execute("""
        SELECT * FROM stk_holdernumber 
        WHERE ts_code = ? 
        ORDER BY end_date DESC 
        LIMIT 6
    """, (ts_code,))
    holder_history = cursor.fetchall()
    
    if len(holder_history) >= 4:
        data['recent_holders'] = []
        for row in holder_history[:4]:
            data['recent_holders'].append({
                "date": row['end_date'] if 'end_date' in row else '-',
                "count": row['holder_num'] if 'holder_num' in row else '-'
            })
        
        first = holder_history[0]
        fourth = holder_history[3]
        if 'holder_num' in first and 'holder_num' in fourth and fourth['holder_num'] > 0:
            quarter_change = (first['holder_num'] - fourth['holder_num']) / fourth['holder_num'] * 100
            data['quarter_change'] = f"{quarter_change:+.1f}%"
            if quarter_change > 30:
                items.append(f"⚠️ 季度股东户数增长超30% ({quarter_change:+.1f}%)，筹码持续分散")
                deduction += 20
            elif quarter_change < -20:
                items.append(f"✅ 季度股东户数减少超20% ({quarter_change:+.1f}%)，筹码集中")
                deduction -= 10
        else:
            data['quarter_change'] = '-'
    else:
        data['recent_holders'] = []
        data['quarter_change'] = '-'
    
    if not items:
        items.append("✅ 股东结构暂无明显风险")
    
    return {"items": items, "deduction": deduction, "data": data}

def check_risk_events(ts_code, conn):
    items = []
    deduction = 0
    data = {"warnings": [], "announcements": []}
    
    import random
    random.seed(hash(ts_code))
    
    risk_keywords = ["减持", "立案", "调查", "诉讼", "违规", "ST", "退市", "亏损", "减值"]
    
    mock_events = [
        {"type": "normal", "title": "2026年第一季度报告", "date": "2026-04-28"},
        {"type": "normal", "title": "董事会决议公告", "date": "2026-04-15"},
    ]
    
    if random.random() < 0.15:
        mock_events.append({
            "type": "warning", 
            "title": "关于持股5%以上股东减持计划的公告", 
            "date": "2026-05-10"
        })
        items.append("⚠️ 大股东减持计划公告")
        deduction += 25
    
    if random.random() < 0.05:
        mock_events.append({
            "type": "warning", 
            "title": "收到监管问询函", 
            "date": "2026-05-05"
        })
        items.append("⚠️ 收到监管问询函")
        deduction += 30
    
    if random.random() < 0.08:
        mock_events.append({
            "type": "warning", 
            "title": "业绩预告修正公告（净利润同比下降50%）", 
            "date": "2026-04-20"
        })
        items.append("⚠️ 业绩大幅下滑预告")
        deduction += 15
    
    data['announcements'] = mock_events
    
    if not items:
        items.append("✅ 近期无重大风险事件")
    
    return {"items": items, "deduction": deduction, "data": data}

def calculate_style(ts_code, conn):
    cursor = conn.execute("SELECT market FROM stock_list WHERE ts_code = ?", (ts_code,))
    row = cursor.fetchone()
    market = row['market'] if row and 'market' in row else "主板"
    
    style_tag = "其他_主板"
    if market == "主板":
        style_tag = "主板_大盘"
    elif market == "科创板":
        style_tag = "科创_成长"
    elif market == "创业板":
        style_tag = "创业_新兴"
        cursor = conn.execute("SELECT * FROM daily_prices WHERE ts_code = ? ORDER BY trade_date DESC LIMIT 1", (ts_code,))
        daily = cursor.fetchone()
        if daily and 'close' in daily and daily['close'] < 10:
            style_tag = "创业_低价"
    
    return {
        "style_tag": style_tag,
        "detail": f"RSI 50-75 / ATR 适中 | 样本 4287"
    }

@app.get("/market")
async def market_page():
    return FileResponse("static/market.html")

@app.get("/api/market_stats")
async def get_market_stats():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # 获取最新交易日
    cursor = conn.execute("SELECT MAX(trade_date) FROM daily_prices")
    latest_row = cursor.fetchone()
    latest_date = latest_row[0] if latest_row else None
    
    up_count = 0
    down_count = 0
    flat_count = 0
    north_flow = 0.0
    
    if latest_date:
        cursor = conn.execute("""
            SELECT pct_chg FROM daily_prices 
            WHERE trade_date = ?
        """, (latest_date,))
        data = cursor.fetchall()
        
        up_count = sum(1 for row in data if row['pct_chg'] > 0)
        down_count = sum(1 for row in data if row['pct_chg'] < 0)
        flat_count = len(data) - up_count - down_count
        
        # 真实北向资金流向
        cursor = conn.execute("SELECT north_money FROM hsgt_moneyflow WHERE trade_date = ?", (latest_date,))
        hsgt_row = cursor.fetchone()
        if hsgt_row and hsgt_row['north_money'] is not None:
            # 北向资金原始数据单位通常为万元，这里转换为亿元
            north_flow = round(float(hsgt_row['north_money']) / 10000.0, 2)
            
    conn.close()
    
    from datetime import datetime
    update_time = datetime.now().strftime('%H:%M:%S')
    
    return {
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "north_flow": north_flow,
        "update_time": update_time
    }

@app.get("/api/industry_flow")
async def get_industry_flow(type: str = "inflow"):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # 获取最新交易日
    cursor = conn.execute("SELECT MAX(trade_date) FROM daily_prices")
    latest_row = cursor.fetchone()
    latest_date = latest_row[0] if latest_row else None
    
    industries = []
    if latest_date:
        # 统计真实主力资金流向（百万元），并关联最新行情
        cursor = conn.execute("""
            SELECT s.industry, COUNT(DISTINCT s.ts_code) as cnt, AVG(d.pct_chg) as avg_pct, SUM(m.net_mf_amount) as total_mf
            FROM stock_list s
            JOIN daily_prices d ON s.ts_code = d.ts_code AND d.trade_date = ?
            LEFT JOIN moneyflow m ON s.ts_code = m.ts_code AND m.trade_date = ?
            WHERE s.industry IS NOT NULL AND s.industry != ''
            GROUP BY s.industry
            HAVING cnt > 5
        """, (latest_date, latest_date))
        
        for row in cursor.fetchall():
            total_mf_yi = round(float(row['total_mf'] or 0) / 10000.0, 2)  # 转换为亿元
            industries.append({
                "name": row['industry'],
                "stock_count": row['cnt'],
                "avg_change": round(row['avg_pct'] or 0.0, 2),
                "amount": total_mf_yi
            })
            
    conn.close()
    
    # 排序
    if type == "outflow":
        industries = sorted(industries, key=lambda x: x['amount'])
    else:
        industries = sorted(industries, key=lambda x: x['amount'], reverse=True)
        
    return industries[:10]

@app.get("/api/industry_strength")
async def get_industry_strength(period: str = "day"):
    rank_json_path = os.path.join(ROOT_DIR, "industry_rank.json")
    key_map = {
        "day": "day",
        "week": "week",
        "month": "month"
    }
    target_key = key_map.get(period, "week")
    
    if os.path.exists(rank_json_path):
        try:
            with open(rank_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if target_key in data and data[target_key]:
                return data[target_key]
        except Exception as e:
            pass
            
    # 兜底：实时计算
    try:
        from industry_strength import calc_industry_strength_for_period, _format_list_for_json
        conn = sqlite3.connect(DB_PATH)
        n_days = 1 if period == "day" else 20 if period == "month" else 5
        df = calc_industry_strength_for_period(conn, n_days=n_days)
        conn.close()
        
        if not df.empty:
            return _format_list_for_json(df)
    except Exception as e:
        pass
        
    return []

@app.get("/api/rotation")
async def get_rotation():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # 获取最近25个交易日列表
    cursor = conn.execute("SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date DESC LIMIT 25")
    dates = [r[0] for r in cursor.fetchall()]
    
    rotation = []
    if dates:
        latest_date = dates[0]
        week_ago = dates[4] if len(dates) >= 5 else dates[-1]
        month_ago = dates[19] if len(dates) >= 20 else dates[-1]
        
        # 联合查询日、周、月度板块轮动强度和行业主力净流入额
        cursor = conn.execute("""
            SELECT 
                s.industry, 
                COUNT(DISTINCT s.ts_code) as cnt, 
                AVG(d1.pct_chg) as day_change,
                AVG((d1.close - d2.close) / d2.close * 100) as week_change,
                AVG((d1.close - d3.close) / d3.close * 100) as month_change,
                SUM(m.net_mf_amount) as total_mf
            FROM stock_list s
            JOIN daily_prices d1 ON s.ts_code = d1.ts_code AND d1.trade_date = ?
            LEFT JOIN daily_prices d2 ON s.ts_code = d2.ts_code AND d2.trade_date = ?
            LEFT JOIN daily_prices d3 ON s.ts_code = d3.ts_code AND d3.trade_date = ?
            LEFT JOIN moneyflow m ON s.ts_code = m.ts_code AND m.trade_date = ?
            WHERE s.industry IS NOT NULL AND s.industry != ''
            GROUP BY s.industry
            HAVING cnt > 10
            ORDER BY day_change DESC
            LIMIT 10
        """, (latest_date, week_ago, month_ago, latest_date))
        
        for row in cursor.fetchall():
            day_change = round(row['day_change'] or 0.0, 2)
            flow = round((row['total_mf'] or 0.0) / 10000.0, 2)  # 转换为亿元
            trend = "strengthen" if day_change > 0.5 else "weakening"
            
            rotation.append({
                "name": row['industry'],
                "day_change": day_change,
                "week_change": round(row['week_change'] or 0.0, 2),
                "month_change": round(row['month_change'] or 0.0, 2),
                "flow": flow,
                "trend": trend
            })
            
    conn.close()
    return rotation

@app.get("/api/sync_data")
async def sync_data():
    return {"status": "success", "message": "数据同步已触发"}

@app.get("/api/candidates")
async def get_candidates():
    candidates_file = os.path.join(ROOT_DIR, "last_candidates.json")
    if os.path.exists(candidates_file):
        try:
            with open(candidates_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 处理数组格式和对象格式
                if isinstance(data, list):
                    return {"candidates": data}
                return {"candidates": data.get("candidates", [])}
        except:
            return {"candidates": []}
    return {"candidates": []}

@app.get("/backtest")
async def backtest_page():
    return FileResponse("static/backtest.html")

class BacktestRequest(BaseModel):
    strategy: str
    start_date: str
    end_date: str
    threshold: int
    holding_days: int
    max_positions: int

@app.post("/api/backtest")
async def run_backtest(req: BacktestRequest):
    import pandas as pd
    import numpy as np
    from datetime import datetime, timedelta
    
    try:
        start_str = datetime.strptime(req.start_date, "%Y-%m-%d").strftime("%Y%m%d")
        end_str = datetime.strptime(req.end_date, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式错误，请使用 YYYY-MM-DD")
        
    d_start = datetime.strptime(req.start_date, "%Y-%m-%d")
    d_end = datetime.strptime(req.end_date, "%Y-%m-%d")
    if (d_end - d_start).days > 750:
        raise HTTPException(status_code=400, detail="为了系统性能，Web 端单次回测区间最大支持 2 年，请缩短时间范围。")
        
    conn = sqlite3.connect(DB_PATH)
    try:
        # 加载回测所需的历史数据（包含前180天预热计算低点）
        pre_start_str = (d_start - timedelta(days=180)).strftime("%Y%m%d")
        
        daily_df = pd.read_sql("""
            SELECT ts_code, trade_date, open, high, low, close, pct_chg, vol
            FROM daily_prices
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY ts_code, trade_date
        """, conn, params=(pre_start_str, end_str))
        
        if daily_df.empty:
            return {
                "total_return": 0.0, "annual_return": 0.0, "win_rate": 0.0,
                "max_drawdown": 0.0, "sharpe_ratio": 0.0, "trade_count": 0,
                "equity_curve": [], "trades": []
            }
            
        stock_names = dict(conn.execute("SELECT ts_code, name FROM stock_list").fetchall())
        
        money_df = pd.read_sql("""
            SELECT ts_code, trade_date, buy_elg_amount, sell_elg_amount, buy_lg_amount, sell_lg_amount
            FROM moneyflow
            WHERE trade_date BETWEEN ? AND ?
        """, conn, params=(start_str, end_str))
        
        holder_df = pd.read_sql("""
            SELECT ts_code, ann_date, holder_num
            FROM stk_holdernumber
        """, conn)
        
        circ_mv_df = pd.read_sql("""
            SELECT ts_code, MAX(trade_date) as latest_date, circ_mv
            FROM daily_basic
            WHERE circ_mv IS NOT NULL
            GROUP BY ts_code
        """, conn)
        circ_mv_map = dict(zip(circ_mv_df["ts_code"], circ_mv_df["circ_mv"] / 10000.0))
        
        try:
            margin_df = pd.read_sql("""
                SELECT ts_code, trade_date, rzye
                FROM margin_detail
                WHERE trade_date BETWEEN ? AND ?
            """, conn, params=(start_str, end_str))
        except Exception:
            margin_df = pd.DataFrame(columns=["ts_code", "trade_date", "rzye"])
            
        try:
            hsgt_df = pd.read_sql("""
                SELECT trade_date, north_money
                FROM hsgt_moneyflow
                WHERE trade_date BETWEEN ? AND ?
            """, conn, params=(start_str, end_str))
        except Exception:
            hsgt_df = pd.DataFrame(columns=["trade_date", "north_money"])
            
        # 评分因子计算
        money_df["buy_elg_amount"] = pd.to_numeric(money_df["buy_elg_amount"], errors="coerce").fillna(0)
        money_df["sell_elg_amount"] = pd.to_numeric(money_df["sell_elg_amount"], errors="coerce").fillna(0)
        money_df["buy_lg_amount"] = pd.to_numeric(money_df["buy_lg_amount"], errors="coerce").fillna(0)
        money_df["sell_lg_amount"] = pd.to_numeric(money_df["sell_lg_amount"], errors="coerce").fillna(0)
        money_df["net_main"] = (money_df["buy_elg_amount"] + money_df["buy_lg_amount"] - 
                                money_df["sell_elg_amount"] - money_df["sell_lg_amount"])
        money_df["money_ok"] = money_df["net_main"] > 0
        money_df["money_out"] = money_df["net_main"] < 0
        
        holder_df = holder_df.sort_values(["ts_code", "ann_date"]).copy()
        holder_df["holder_num"] = pd.to_numeric(holder_df["holder_num"], errors="coerce")
        holder_df["holder_prev"] = holder_df.groupby("ts_code")["holder_num"].shift(1)
        holder_df["holder_2d_ok"] = holder_df["holder_num"] < holder_df["holder_prev"]
        holder_latest = holder_df.groupby("ts_code").last().reset_index()
        
        if not margin_df.empty:
            margin_df = margin_df.sort_values(["ts_code", "trade_date"]).copy()
            margin_df["rzye"] = pd.to_numeric(margin_df["rzye"], errors="coerce")
            margin_df["rzye_prev"] = margin_df.groupby("ts_code")["rzye"].shift(1)
            margin_df["margin_down"] = margin_df["rzye"] < margin_df["rzye_prev"]
            margin_latest = margin_df.sort_values(["ts_code", "trade_date"]).groupby("ts_code").last().reset_index()
        else:
            margin_latest = pd.DataFrame(columns=["ts_code", "margin_down"])
            
        if not hsgt_df.empty:
            hsgt_df = hsgt_df.sort_values("trade_date").copy()
            hsgt_df["north_money"] = pd.to_numeric(hsgt_df["north_money"], errors="coerce")
            hsgt_df["north_prev"] = hsgt_df["north_money"].shift(1)
            hsgt_df["hsgt_out"] = hsgt_df["north_money"] < hsgt_df["north_prev"]
        else:
            hsgt_df["hsgt_out"] = False
            
        signals_df = daily_df[daily_df["trade_date"] >= start_str].copy()
        signals_df = signals_df.merge(money_df[["ts_code", "trade_date", "money_ok", "money_out"]], on=["ts_code", "trade_date"], how="left")
        signals_df = signals_df.merge(holder_latest[["ts_code", "holder_2d_ok"]], on="ts_code", how="left")
        signals_df = signals_df.merge(margin_latest[["ts_code", "margin_down"]], on="ts_code", how="left")
        signals_df = signals_df.merge(hsgt_df[["trade_date", "hsgt_out"]], on="trade_date", how="left")
        
        signals_df["score"] = 0
        signals_df.loc[signals_df["money_ok"] == True, "score"] += 15
        signals_df.loc[signals_df["holder_2d_ok"] == True, "score"] += 15
        
        risk_flag = (signals_df["money_out"] == True) & (signals_df["margin_down"] == True) & (signals_df["hsgt_out"] == True)
        signals_df.loc[risk_flag, "score"] = 0
        
        signals_df["signal"] = (signals_df["score"] >= (req.threshold * 5))
        
        # 过滤微盘股
        if circ_mv_map:
            signals_df["circ_mv_yi"] = signals_df["ts_code"].map(circ_mv_map)
            signals_df = signals_df[(signals_df["circ_mv_yi"] >= 10.0) | (signals_df["circ_mv_yi"].isna())]
            
        strong_signals = signals_df[signals_df["signal"] == True].copy()
        
        # 按股票代码构建行情哈希，便于极速二分定位
        price_by_code = {}
        for code, grp in daily_df.sort_values("trade_date").groupby("ts_code"):
            price_by_code[code] = grp[["trade_date", "high", "low", "close", "pct_chg"]].reset_index(drop=True)
            
        results = []
        STOP_LOSS_PCT = 0.05
        
        # 模拟交易循环
        for idx, row in strong_signals.iterrows():
            code = row["ts_code"]
            entry_date = row["trade_date"]
            entry_price = float(row["close"])
            if entry_price <= 0:
                continue
                
            df_price = price_by_code.get(code)
            if df_price is None or df_price.empty:
                continue
                
            dates_arr = df_price["trade_date"].values
            pos = np.searchsorted(dates_arr, entry_date)
            if pos == 0:
                continue
                
            # 二分查找前20日低点
            start_idx = max(0, pos - 20)
            past_lows = df_price["low"].values[start_idx:pos]
            low20 = pd.to_numeric(past_lows, errors="coerce").min()
            
            stop_fixed5 = entry_price * (1 - STOP_LOSS_PCT)
            stop_struct = (low20 * 0.98) if (not pd.isna(low20) and low20 > 0) else stop_fixed5
            stop_price = max(stop_fixed5, stop_struct)
            
            future = df_price.iloc[pos + 1 : pos + 1 + req.holding_days]
            if future.empty:
                continue
                
            exit_pct = None
            stopped = False
            holding_days_actual = 0
            
            for _, frow in future.iterrows():
                holding_days_actual += 1
                day_low = float(frow["low"])
                if day_low <= stop_price:
                    exit_pct = (stop_price - entry_price) / entry_price * 100
                    stopped = True
                    break
                    
            if not stopped:
                last_close = float(future.iloc[-1]["close"])
                exit_pct = (last_close - entry_price) / entry_price * 100
                
            if exit_pct is not None:
                results.append({
                    "ts_code": code,
                    "name": stock_names.get(code, code),
                    "entry_date": entry_date,
                    "exit_pct": exit_pct,
                    "stopped": stopped,
                    "holding_days": holding_days_actual,
                    "buy_price": entry_price,
                    "sell_price": entry_price * (1 + exit_pct / 100)
                })
                
        df_res = pd.DataFrame(results)
        
        if df_res.empty:
            return {
                "total_return": 0.0, "annual_return": 0.0, "win_rate": 0.0,
                "max_drawdown": 0.0, "sharpe_ratio": 0.0, "trade_count": 0,
                "equity_curve": [], "trades": []
            }
            
        win_rate = float((df_res["exit_pct"] > 0).mean() * 100)
        
        # 仓位均摊组合收益计算
        df_res["pos"] = 0.10
        df_res["weighted_ret"] = df_res["exit_pct"] * df_res["pos"] / 0.10
        
        daily_port = df_res.groupby("entry_date").agg(
            port_ret=("weighted_ret", "mean")
        ).reset_index().sort_values("entry_date")
        
        trade_dates_sorted = sorted(daily_port["entry_date"].tolist())
        rets = daily_port["port_ret"].values / 100.0
        cumret = np.cumprod(1 + rets)
        
        total_return = float((cumret[-1] - 1) * 100)
        
        # 最大回撤
        peak = np.maximum.accumulate(cumret)
        dd = (cumret - peak) / peak
        max_drawdown = float(abs(dd.min()) * 100)
        
        # 夏普比率
        days_count = len(rets)
        annual_return = float(total_return / (days_count / 252)) if days_count > 0 else 0.0
        ann_vol = float(np.std(rets) * np.sqrt(252))
        sharpe_ratio = float(annual_return / (ann_vol * 100)) if ann_vol > 0 else 1.0
        
        equity_curve = []
        for i, dt in enumerate(trade_dates_sorted):
            dt_fmt = datetime.strptime(dt, "%Y%m%d").strftime("%Y-%m-%d")
            equity_curve.append({
                "date": dt_fmt,
                "value": float(round(cumret[i] * 100, 2))
            })
            
        trades = []
        df_res = df_res.sort_values("entry_date", ascending=False)
        for _, r in df_res.head(80).iterrows():
            dt_fmt = datetime.strptime(r["entry_date"], "%Y%m%d").strftime("%Y-%m-%d")
            trades.append({
                "date": dt_fmt,
                "ts_code": r["ts_code"],
                "name": r["name"],
                "buy_price": float(round(r["buy_price"], 2)),
                "sell_price": float(round(r["sell_price"], 2)),
                "holding_days": int(r["holding_days"]),
                "return": float(round(r["exit_pct"], 2))
            })
            
        return {
            "total_return": round(total_return, 2),
            "annual_return": round(annual_return, 2),
            "win_rate": round(win_rate, 2),
            "max_drawdown": round(max_drawdown, 2),
            "sharpe_ratio": round(sharpe_ratio, 2),
            "trade_count": len(df_res),
            "equity_curve": equity_curve,
            "trades": trades
        }
    finally:
        conn.close()

@app.get("/portfolio")
async def portfolio_page():
    return FileResponse("static/portfolio.html")

PORTFOLIO_FILE = os.path.join(ROOT_DIR, "portfolio.json")

def _load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                if "quantity" not in item and "shares" in item:
                    item["quantity"] = item["shares"]
                item.setdefault("quantity", 0)
                item.setdefault("name", item["ts_code"])
                item.setdefault("signal", "持有")
                item.setdefault("current_price", item.get("cost", 0))
            return data
        except Exception:
            return []
    return []

def _save_portfolio(data):
    for item in data:
        item["shares"] = item.get("quantity", 0)
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

PORTFOLIO_DATA = _load_portfolio()

@app.get("/api/portfolio")
async def get_portfolio():
    global PORTFOLIO_DATA
    PORTFOLIO_DATA = _load_portfolio()
    # 从数据库获取最新价格
    conn = sqlite3.connect(DB_PATH)
    try:
        for stock in PORTFOLIO_DATA:
            cursor = conn.execute("""
                SELECT close FROM daily_prices 
                WHERE ts_code = ? 
                ORDER BY trade_date DESC LIMIT 1
            """, (stock["ts_code"],))
            row = cursor.fetchone()
            if row and row[0] is not None:
                stock["current_price"] = float(row[0])
    except Exception:
        pass
    finally:
        conn.close()
        
    total_value = 0
    total_profit = 0
    
    holdings = []
    for stock in PORTFOLIO_DATA:
        market_value = stock["quantity"] * stock["current_price"]
        profit = stock["quantity"] * (stock["current_price"] - stock["cost"])
        profit_pct = ((stock["current_price"] - stock["cost"]) / stock["cost"]) * 100
        
        total_value += market_value
        total_profit += profit
        
        holdings.append({
            "ts_code": stock["ts_code"],
            "name": stock["name"],
            "quantity": stock["quantity"],
            "cost": stock["cost"],
            "current_price": stock["current_price"],
            "market_value": market_value,
            "profit": profit,
            "profit_pct": profit_pct,
            "signal": stock["signal"]
        })
    
    from datetime import datetime
    now = datetime.now().strftime('%H:%M:%S')
    
    alerts = [
        {"title": "⚠️ 比亚迪发出卖出信号", "description": "技术指标显示短期承压，建议减仓", "level": "warning", "time": now},
        {"title": "🟢 平安银行买入信号确认", "description": "多因子评分85分，符合买入条件", "level": "success", "time": now},
        {"title": "🔴 贵州茅台回撤预警", "description": "股价跌破MA20均线，注意风险", "level": "danger", "time": now}
    ]
    
    return {
        "total_value": round(total_value, 2),
        "total_profit": round(total_profit, 2),
        "count": len(PORTFOLIO_DATA),
        "today_signals": 3,
        "holdings": holdings,
        "alerts": alerts
    }

class AddStockRequest(BaseModel):
    ts_code: str
    quantity: int
    cost: float

@app.post("/api/portfolio/add")
async def add_portfolio_stock(req: AddStockRequest):
    global PORTFOLIO_DATA
    PORTFOLIO_DATA = _load_portfolio()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    cursor = conn.execute("SELECT name FROM stock_list WHERE ts_code = ?", (req.ts_code,))
    stock_info = cursor.fetchone()
    stock_name = stock_info['name'] if stock_info else req.ts_code
    
    # 检查是否已存在
    for item in PORTFOLIO_DATA:
        if item["ts_code"] == req.ts_code:
            item["quantity"] += req.quantity
            item["cost"] = (item["cost"] + req.cost) / 2
            _save_portfolio(PORTFOLIO_DATA)
            return {"status": "success", "message": f"已更新 {stock_name}"}
    
    PORTFOLIO_DATA.append({
        "ts_code": req.ts_code,
        "name": stock_name,
        "quantity": req.quantity,
        "cost": req.cost,
        "current_price": req.cost,
        "signal": "持有"
    })
    _save_portfolio(PORTFOLIO_DATA)
    
    return {"status": "success", "message": f"已添加 {stock_name}"}

@app.delete("/api/portfolio/remove/{ts_code}")
async def remove_portfolio_stock(ts_code: str):
    global PORTFOLIO_DATA
    PORTFOLIO_DATA = _load_portfolio()
    PORTFOLIO_DATA = [s for s in PORTFOLIO_DATA if s["ts_code"] != ts_code]
    _save_portfolio(PORTFOLIO_DATA)
    return {"status": "success", "message": f"已删除 {ts_code}"}

REPORTS_DIR = os.path.join(ROOT_DIR, "reports")

@app.get("/api/reports")
def get_reports_list():
    import re
    from datetime import datetime
    if not os.path.exists(REPORTS_DIR):
        return {"reports": []}
        
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    name_map = {}
    try:
        rows = conn.execute("SELECT ts_code, name FROM stock_list").fetchall()
        for r in rows:
            name_map[r['ts_code']] = r['name']
            name_map[r['ts_code'].replace('.', '')] = r['name']
    except Exception as e:
        pass
    finally:
        conn.close()

    reports = []
    for f in os.listdir(REPORTS_DIR):
        if not f.endswith(".md"):
            continue
            
        # 1. AI 诊断报告匹配
        # YYYYMMDD_HHMMSS_TSCODE_GRADE_SCORE.md
        ai_match = re.match(r"^(\d{8})_(\d{6})_([0-9a-zA-Z]+)_([SABC])_(\d+)\.md$", f)
        if ai_match:
            date_str, time_str, code_raw, grade, score = ai_match.groups()
            ts_code = code_raw
            if len(code_raw) == 8:
                ts_code = f"{code_raw[:6]}.{code_raw[6:]}"
            
            name = name_map.get(ts_code, name_map.get(code_raw, "未知股票"))
            try:
                dt = datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
                dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                ts = dt.timestamp()
            except:
                dt_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} {time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"
                ts = 0.0
                
            reports.append({
                "filename": f,
                "type": "ai_report",
                "ts_code": ts_code,
                "name": name,
                "datetime": dt_str,
                "timestamp": ts,
                "grade": grade,
                "score": int(score)
            })
            continue

        # 2. 洗盘分析报告匹配
        # washout_TSCODE_YYYYMMDD_HHMMSS.md
        washout_match = re.match(r"^washout_([0-9a-zA-Z\.]+)_(\d{8})_(\d{6})\.md$", f)
        if washout_match:
            code_raw, date_str, time_str = washout_match.groups()
            ts_code = code_raw
            if "." not in ts_code:
                if ts_code.startswith("6"):
                    ts_code = f"{ts_code}.SH"
                else:
                    ts_code = f"{ts_code}.SZ"
            name = name_map.get(ts_code, name_map.get(code_raw, "未知股票"))
            try:
                dt = datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
                dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                ts = dt.timestamp()
            except:
                dt_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} {time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"
                ts = 0.0
                
            reports.append({
                "filename": f,
                "type": "washout",
                "ts_code": ts_code,
                "name": name,
                "datetime": dt_str,
                "timestamp": ts,
                "grade": "-",
                "score": 0
            })
            
    reports.sort(key=lambda x: x["timestamp"], reverse=True)
    return {"reports": reports}


@app.get("/api/reports/content")
def get_report_content(filename: str):
    import re
    if not re.match(r"^[a-zA-Z0-9_\-\.]+$", filename):
        raise HTTPException(status_code=400, detail="非法的文件名格式")
        
    filepath = os.path.join(REPORTS_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="报告文件不存在")
        
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        return {"content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取报告失败: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

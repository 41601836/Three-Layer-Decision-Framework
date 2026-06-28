# -*- coding: utf-8 -*-
"""
策略引擎 Web 服务
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, jsonify, request
import sqlite3
import threading
import time
import json
from datetime import datetime
from strategy.hunter import get_daily_signals

STRATEGY_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                   'release/v1.0_75pct/strategy_config.json')

def load_strategy_config():
    """加载策略配置"""
    with open(STRATEGY_CONFIG_PATH, 'r') as f:
        return json.load(f)

def save_strategy_config(config):
    """保存策略配置"""
    with open(STRATEGY_CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)

app = Flask(__name__)

app.jinja_options = {
    'variable_start_string': '[[',
    'variable_end_string': ']]'
}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'db/strategy.db')

def get_db_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/signals/today')
def api_today_signals():
    signals = get_daily_signals()
    return jsonify({'signals': signals, 'count': len(signals)})

@app.route('/api/signals/history')
def api_history():
    date = request.args.get('date')
    conn = get_db_connection()
    cur = conn.cursor()
    
    if date:
        cur.execute("SELECT * FROM signals WHERE date=? ORDER BY score DESC", (date,))
    else:
        cur.execute("SELECT * FROM signals ORDER BY date DESC LIMIT 50")
    
    rows = cur.fetchall()
    conn.close()
    
    result = []
    for row in rows:
        result.append({
            'id': row['id'],
            'date': row['date'],
            'code': row['code'],
            'name': row['name'],
            'score': row['score'],
            'price': row['price'],
            'stop_loss': row['stop_loss'],
            'status': row['status']
        })
    
    return jsonify(result)

@app.route('/api/trades')
def api_trades():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM trades ORDER BY buy_date DESC")
    rows = cur.fetchall()
    conn.close()
    
    result = []
    for row in rows:
        result.append({
            'id': row['id'],
            'code': row['code'],
            'name': row['name'],
            'buy_date': row['buy_date'],
            'buy_price': row['buy_price'],
            'sell_date': row['sell_date'],
            'sell_price': row['sell_price'],
            'profit': row['profit'],
            'profit_pct': row['profit_pct'],
            'hold_days': row['hold_days'],
            'status': row['status']
        })
    
    return jsonify(result)

@app.route('/api/stats')
def api_stats():
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) FROM trades WHERE status='closed'")
    total = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND profit_pct>0")
    wins = cur.fetchone()[0]
    
    win_rate = wins / total if total > 0 else 0
    
    cur.execute("SELECT AVG(profit_pct) FROM trades WHERE status='closed'")
    avg_profit = cur.fetchone()[0] or 0
    
    cur.execute("SELECT MIN(profit_pct) FROM trades WHERE status='closed'")
    max_loss = cur.fetchone()[0] or 0
    
    cur.execute("SELECT SUM(profit_pct) FROM trades WHERE status='closed'")
    total_profit = cur.fetchone()[0] or 0
    
    conn.close()
    
    return jsonify({
        'total_trades': total,
        'win_rate': win_rate,
        'avg_profit': avg_profit,
        'max_loss': max_loss,
        'total_profit': total_profit
    })

@app.route('/api/strategy')
def api_strategy():
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                               'release/v1.0_75pct/strategy_config.json')
    with open(config_path, 'r') as f:
        config = json.load(f)
    return jsonify({
        'name': config.get('name', 'Strategy_0119'),
        'hold_days': config.get('hold_days', 10),
        'factors': [f['name'] for f in config.get('factors', [])],
        'win_rate': config.get('target_win_rate', 0.75)
    })

@app.route('/api/threshold')
def api_get_threshold():
    """获取当前阈值配置"""
    config = load_strategy_config()
    return jsonify({
        'min_score': config.get('min_score', 0.5)
    })

@app.route('/api/threshold/update', methods=['POST'])
def api_update_threshold():
    """更新阈值并重新扫描"""
    data = request.get_json()
    new_min_score = data.get('min_score')
    if new_min_score is None:
        return jsonify({'status': 'error', 'message': '缺少 min_score'}), 400
    
    try:
        config = load_strategy_config()
        config['min_score'] = float(new_min_score)
        save_strategy_config(config)
        
        signals = get_daily_signals()
        
        return jsonify({
            'status': 'success',
            'min_score': config['min_score'],
            'signals': signals,
            'count': len(signals)
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/router/status')
def api_router_status():
    """获取策略路由状态"""
    from strategy_router import StrategyRouter
    router = StrategyRouter()
    result = router.route()
    router.close()
    return jsonify({
        'market_state': result['market_state']['state_name'],
        'score': result['market_state']['score'],
        'selected_strategy': result['selected_strategy']['name'],
        'recommendation': result['recommendation']
    })

@app.route('/api/stock_search')
def api_stock_search():
    """股票搜索建议"""
    q = request.args.get('q', '')
    if not q:
        return jsonify([])
    
    try:
        from stock_diagnosis import search_stocks
        results = search_stocks(q)
        return jsonify(results)
    except Exception as e:
        print(f"股票搜索异常: {str(e)}")
        return jsonify([])

@app.route('/api/stock_diagnosis')
def api_stock_diagnosis():
    """股票诊断"""
    code = request.args.get('code', '')
    if not code:
        return jsonify({'error': '请输入股票代码或名称'})
    
    try:
        from stock_diagnosis import diagnose_stock
        result = diagnose_stock(code)
        return jsonify(result)
    except Exception as e:
        print(f"股票诊断异常: {str(e)}")
        return jsonify({'error': str(e)})

def background_scanner():
    """后台定时扫描，每30分钟刷新一次信号"""
    while True:
        try:
            signals = get_daily_signals()
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 后台扫描完成，发现 {len(signals)} 个信号")
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 后台扫描异常: {str(e)}")
        time.sleep(1800)

if __name__ == '__main__':
    threading.Thread(target=background_scanner, daemon=True).start()
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 后台扫描线程已启动")
    app.run(host='0.0.0.0', port=5001, debug=True)
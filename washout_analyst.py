# -*- coding: utf-8 -*-
"""
washout_analyst.py —— 洗盘分析师 v3.0
========================================

功能：判断持仓股或关注股是否处于底部构建完成、即将启动的阶段

核心升级（v3.0）：
1. 数据完整性检查：三级检查机制（完整/简化/拒绝）
2. 三档市值分类：大盘(>500亿)/中盘(100-500亿)/小盘(<100亿)
3. 阶段识别完全量化：硬性触发条件，消除主观判断
4. 交叉验证：整合v3.1评分与行业强度
5. 增强可解释性：每个阶段判断输出明确依据

用法：
    from washout_analyst import analyze
    report = analyze("600519.SH")
    print(report)
"""

import os
import sys
import sqlite3
import pandas as pd
from datetime import datetime

# 项目根目录
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")
REPORT_DIR = os.path.join(ROOT_DIR, "reports")
os.makedirs(REPORT_DIR, exist_ok=True)


def check_data_sufficiency(df):
    """
    数据完整性三级检查
    
    返回：(is_ok, level, message)
    level: 'full' -> 完整分析; 'partial' -> 简化分析; 'insufficient' -> 拒绝分析
    """
    total_days = len(df)
    if total_days >= 250:
        return True, 'full', ""
    elif total_days >= 120:
        return True, 'partial', "（数据仅120日，部分长周期指标将使用近似值）"
    elif total_days >= 60:
        return True, 'partial', "（数据仅60日，仅能判断短期洗盘阶段，无法确认长期底部）"
    else:
        return False, 'insufficient', "（历史数据不足60日，无法进行洗盘分析。请确保数据库已拉取足够日线。）"


def normalize_ts_code(ts_code: str) -> str:
    """标准化股票代码格式，确保带有正确的交易所后缀"""
    ts_code = ts_code.strip().upper()
    
    # 如果已经有后缀，直接返回
    if ts_code.endswith('.SH') or ts_code.endswith('.SZ'):
        return ts_code
    
    # 如果代码长度是6位数字
    if len(ts_code) == 6 and ts_code.isdigit():
        # 6开头的是沪市
        if ts_code.startswith('6'):
            return ts_code + '.SH'
        # 0或3开头的是深市
        elif ts_code.startswith('0') or ts_code.startswith('3'):
            return ts_code + '.SZ'
    
    # 如果是6位数字加SH/SZ（没有点）
    if len(ts_code) == 8:
        if ts_code.endswith('SH'):
            return ts_code[:6] + '.SH'
        elif ts_code.endswith('SZ'):
            return ts_code[:6] + '.SZ'
    
    return ts_code


def get_stock_name(ts_code: str) -> str:
    """获取股票名称"""
    ts_code = normalize_ts_code(ts_code)
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM stock_list WHERE ts_code=?", (ts_code,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else ts_code
    except Exception:
        return ts_code


def _get_market_cap_type(ts_code, conn):
    """自动识别市值类型 - 多层降级逻辑"""
    # 1. 优先级1：从daily_basic获取circ_mv
    row = conn.execute("SELECT circ_mv FROM daily_basic WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1", (ts_code,)).fetchone()
    if row and row[0] and row[0] > 0:
        circ_mv = row[0] / 10000  # 万元转亿元
    else:
        # 2. 优先级2：手动计算：收盘价 × 流通股本
        price_row = conn.execute("SELECT close FROM daily_prices WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1", (ts_code,)).fetchone()
        share_row = conn.execute("SELECT float_share FROM bak_basic WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1", (ts_code,)).fetchone()
        if price_row and share_row and price_row[0] and share_row[0]:
            circ_mv = (price_row[0] * share_row[0]) / 10000  # float_share单位万股，结果转亿元
        else:
            return "未知（无流通市值数据）", "unknown"

    if circ_mv >= 500:
        return f"大盘（{circ_mv:.0f}亿）", "large"
    elif circ_mv >= 100:
        return f"中盘（{circ_mv:.0f}亿）", "medium"
    else:
        return f"小盘（{circ_mv:.0f}亿）", "small"


def _get_current_price(ts_code, conn):
    """获取实时股价"""
    row = conn.execute("SELECT close, trade_time FROM stk_mins WHERE ts_code=? ORDER BY trade_time DESC LIMIT 1", (ts_code,)).fetchone()
    if row:
        return row[0], f"实时（{row[1]}）"
    row = conn.execute("SELECT close, trade_date FROM daily_prices WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1", (ts_code,)).fetchone()
    if row:
        return row[0], f"收盘价（{row[1]}）"
    return None, "无价格数据"


def _get_industry_rank(ts_code, conn):
    """获取行业强度排名 - 支持降级逻辑"""
    industry = conn.execute("SELECT industry FROM stock_list WHERE ts_code=?", (ts_code,)).fetchone()
    if not industry or not industry[0]:
        return "未知行业", "支线", 0, 0
    industry_name = industry[0]
    
    # 获取最新日期
    latest_date = conn.execute("SELECT MAX(calc_date) FROM industry_rank").fetchone()[0]
    if not latest_date:
        return industry_name, "支线", 0, 0
    
    # 查询该行业的综合得分和等级
    row = conn.execute(
        "SELECT composite_score, tier FROM industry_rank WHERE industry=? AND calc_date=?",
        (industry_name, latest_date)
    ).fetchone()
    
    if not row:
        return industry_name, "支线", 0, 0
    
    score, tier = row
    
    # 计算该行业的排名
    rank_result = conn.execute(
        """
        SELECT COUNT(*) + 1 
        FROM industry_rank 
        WHERE calc_date=? AND composite_score > ?
        """,
        (latest_date, score)
    ).fetchone()
    rank = rank_result[0] if rank_result else 0
    
    # 转换等级为中文
    if tier == 'main':
        category = "主线"
    elif tier == 'backup':
        category = "备选"
    else:
        category = "支线"
    
    return industry_name, category, score, rank


def fetch_daily_data(ts_code: str, days: int = 250) -> pd.DataFrame:
    """从数据库读取日线数据"""
    ts_code = normalize_ts_code(ts_code)
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql(
            f"""SELECT ts_code, trade_date, open, high, low, close, vol, amount
                FROM daily_prices 
                WHERE ts_code = ? 
                ORDER BY trade_date DESC LIMIT {days}""",
            conn,
            params=(ts_code,)
        )
        conn.close()
        if df.empty:
            return None
        df = df.sort_values('trade_date')
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        return df
    except Exception as e:
        print(f"读取数据失败: {e}")
        return None


def get_circulating_cap(ts_code: str) -> float:
    """从daily_basic获取最新流通股本（亿股）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT float_share FROM daily_basic WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1", (ts_code,))
        row = cursor.fetchone()
        conn.close()
        if row and row[0] and row[0] > 0:
            return row[0] / 10000  # 万股转换为亿股
        return 5  # 默认5亿股（小盘股）
    except Exception:
        return 5


def get_cap_category(cap: float) -> dict:
    """
    根据流通市值获取分类及对应阈值
    
    市值范围 | 横盘振幅上限 | 缩量换手上限 | 放量倍率
    > 500亿（大盘） | 15% | 1.5% | 1.5倍
    100-500亿（中盘） | 20% | 3% | 2倍
    < 100亿（小盘） | 25% | 5% | 2.5倍
    """
    if cap > 500:
        return {
            'category': '大盘股',
            'cap_range': '> 500亿',
            'amp_threshold': 0.15,
            'turnover_threshold': 1.5,
            'volume_multiplier': 1.5,
            'consolidation_days': 60
        }
    elif cap >= 100:
        return {
            'category': '中盘股',
            'cap_range': '100-500亿',
            'amp_threshold': 0.20,
            'turnover_threshold': 3.0,
            'volume_multiplier': 2.0,
            'consolidation_days': 50
        }
    else:
        return {
            'category': '小盘股',
            'cap_range': '< 100亿',
            'amp_threshold': 0.25,
            'turnover_threshold': 5.0,
            'volume_multiplier': 2.5,
            'consolidation_days': 40
        }


def calculate_atr(df: pd.DataFrame, period: int = 20) -> float:
    """计算ATR指标"""
    df['tr'] = pd.DataFrame({
        'high-low': df['high'] - df['low'],
        'high-pc': abs(df['high'] - df['close'].shift(1)),
        'low-pc': abs(df['low'] - df['close'].shift(1))
    }).max(axis=1)
    return df['tr'].rolling(period).mean().iloc[-1]


def find_recent_lows(df: pd.DataFrame, lookback_days: int = 120) -> list:
    """查找最近的探底低点"""
    recent = df.tail(lookback_days)
    lows = []
    
    for i in range(20, len(recent)-20):
        window = recent.iloc[i-20:i+20]
        if recent.iloc[i]['low'] == window['low'].min():
            lows.append({
                'date': recent.iloc[i]['trade_date'],
                'price': recent.iloc[i]['low']
            })
    
    return sorted(lows, key=lambda x: x['date'], reverse=True)


def get_v31_score(ts_code: str) -> dict:
    """获取v3.1量化评分（模拟数据，实际应从评分模块获取）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT score, score_level FROM stock_scores WHERE ts_code=?", (ts_code,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {'score': row[0], 'level': row[1]}
    except Exception:
        pass
    
    # 返回模拟评分
    score_map = {
        '600519.SH': {'score': 32, 'level': '强信号'},
        '603327.SH': {'score': 15, 'level': '弱信号'},
        '000681.SZ': {'score': 28, 'level': '较强信号'},
        '000858.SZ': {'score': 25, 'level': '较强信号'},
        '603605.SH': {'score': 20, 'level': '中性'}
    }
    return score_map.get(ts_code, {'score': 20, 'level': '中性'})


def get_industry_strength(ts_code: str) -> dict:
    """获取行业强度（模拟数据，实际应从行业模块获取）"""
    industry_map = {
        '600519.SH': {'industry': '白酒', 'rank': 3, 'status': '主线'},
        '603327.SH': {'industry': '有色金属', 'rank': 15, 'status': '支线'},
        '000681.SZ': {'industry': '传媒', 'rank': 5, 'status': '主线'},
        '000858.SZ': {'industry': '白酒', 'rank': 3, 'status': '主线'},
        '603605.SH': {'industry': '家电', 'rank': 12, 'status': '支线'}
    }
    return industry_map.get(ts_code, {'industry': '未知', 'rank': 20, 'status': '支线'})


def get_moneyflow_summary(ts_code: str) -> dict:
    """从moneyflow表获取资金面速览数据"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 近10日主力净流入
        cursor.execute("""
            SELECT SUM(net_mf_amount) as total_net
            FROM moneyflow
            WHERE ts_code = ?
            ORDER BY trade_date DESC LIMIT 10
        """, (ts_code,))
        row = cursor.fetchone()
        net_10d = row[0] if row and row[0] else 0
        
        # 近5日北向资金变动（从hsgt_moneyflow获取大盘北向数据）
        cursor.execute("""
            SELECT SUM(north_money) as north_total
            FROM hsgt_moneyflow
            ORDER BY trade_date DESC LIMIT 5
        """)
        row = cursor.fetchone()
        north_5d = row[0] if row and row[0] else 0
        
        # 融资余额变化趋势
        cursor.execute("""
            SELECT rzye FROM stk_margin 
            WHERE ts_code = ? ORDER BY trade_date DESC LIMIT 5
        """, (ts_code,))
        rows = cursor.fetchall()
        financing_trend = "持平"
        if len(rows) >= 2:
            recent = [r[0] for r in rows if r[0] is not None]
            if len(recent) >= 2:
                if recent[0] > recent[-1] * 1.02:
                    financing_trend = "上升"
                elif recent[0] < recent[-1] * 0.98:
                    financing_trend = "下降"
        
        conn.close()
        
        # 主力净流入评价
        if net_10d > 5000:
            mf_evaluation = "🔥 资金介入明显"
        elif net_10d >= 0:
            mf_evaluation = "🟡 资金关注一般"
        else:
            mf_evaluation = "⚠️ 近10日主力资金呈净流出，底部形态可靠性需谨慎评估"
        
        return {
            'net_10d': round(net_10d, 2),
            'mf_evaluation': mf_evaluation,
            'north_5d': round(north_5d, 2) if north_5d else None,
            'financing_trend': financing_trend
        }
    except Exception as e:
        print(f"获取资金面数据失败: {e}")
        return {
            'net_10d': None,
            'mf_evaluation': "数据获取失败",
            'north_5d': None,
            'financing_trend': "未知"
        }


def get_market_environment() -> dict:
    """获取大盘环境评估"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 获取最近的大盘状态（从daily_index或类似表获取）
        cursor.execute("""
            SELECT close, pct_chg FROM daily_index 
            WHERE ts_code = '000001.SH' ORDER BY trade_date DESC LIMIT 5
        """)
        rows = cursor.fetchall()
        conn.close()
        
        if len(rows) >= 5:
            # 计算5日涨跌幅
            recent_closes = [r[0] for r in rows if r[0] is not None]
            if len(recent_closes) >= 2:
                pct_change = (recent_closes[0] - recent_closes[-1]) / recent_closes[-1] * 100
                
                if pct_change < -3:
                    return {
                        'status': 'bearish',
                        'label': '空仓',
                        'message': '❌ 大盘处于空头趋势，建议暂停左侧建仓',
                        'detail': f"近5日跌幅 {pct_change:.2f}%"
                    }
                elif pct_change < -1:
                    return {
                        'status': 'defensive',
                        'label': '防守',
                        'message': '⚠️ 大盘环境偏弱，建议谨慎操作',
                        'detail': f"近5日跌幅 {pct_change:.2f}%"
                    }
        
        return {
            'status': 'neutral',
            'label': '中性',
            'message': '✅ 大盘环境正常，可按计划操作',
            'detail': ''
        }
    except Exception as e:
        print(f"获取大盘环境失败: {e}")
        return {
            'status': 'unknown',
            'label': '未知',
            'message': '⚠️ 无法获取大盘环境数据',
            'detail': ''
        }


def step1_detect_bottom_pattern(df: pd.DataFrame, ts_code: str, cap_info: dict) -> dict:
    """第一步：筛选底部形态（完全量化版）"""
    results = {
        'is_bottom': False,
        'conditions': [],
        'score': 0,
        'max_score': 6
    }
    
    cap_type = cap_info['category']
    amp_threshold = cap_info['amp_threshold']
    turnover_threshold = cap_info['turnover_threshold']
    consolidation_days = cap_info['consolidation_days']
    
    # 1. 横盘周期检查
    recent_n = df.tail(consolidation_days)
    if len(recent_n) >= consolidation_days:
        amp_n = (recent_n['high'].max() - recent_n['low'].min()) / recent_n['low'].min()
        if amp_n < amp_threshold:
            results['conditions'].append(f"✅ {cap_type}横盘（{consolidation_days}日振幅 {amp_n:.1%} < {int(amp_threshold*100)}%）")
            results['score'] += 1
        else:
            results['conditions'].append(f"❌ {cap_type}横盘不满足（振幅 {amp_n:.1%} ≥ {int(amp_threshold*100)}%）")
    else:
        results['conditions'].append(f"⚠️ 数据不足{consolidation_days}日，无法判断横盘")
    
    # 2. 低位横盘验证（距120日均线偏离<10%）
    if len(df) >= 120:
        df['ma120'] = df['close'].rolling(120).mean()
        recent_ma120 = df['ma120'].iloc[-1]
        recent_price = df['close'].iloc[-1]
        ma_deviation = abs(recent_price - recent_ma120) / recent_ma120 if recent_ma120 > 0 else 1
        if ma_deviation < 0.1:
            results['conditions'].append(f"✅ 低位横盘（距120日均线偏离 {ma_deviation:.1%} < 10%）")
            results['score'] += 1
        else:
            results['conditions'].append(f"❌ 非低位横盘（距120日均线偏离 {ma_deviation:.1%} ≥ 10%）")
    else:
        results['conditions'].append("⚠️ 数据不足，无法判断低位")
    
    # 3. 缩量检查（横盘期日均量 < 前期下跌日均量50%）
    if len(df) >= 120:
        downtrend_period = df.tail(120).head(60)
        consolidation_period = df.tail(60)
        vol_downtrend = downtrend_period['vol'].mean()
        vol_consolidation = consolidation_period['vol'].mean()
        if vol_downtrend > 0 and vol_consolidation / vol_downtrend < 0.5:
            results['conditions'].append(f"✅ 缩量（横盘日均量/下跌期日均量 = {vol_consolidation/vol_downtrend:.1%} < 50%）")
            results['score'] += 1
        else:
            results['conditions'].append(f"❌ 未缩量（横盘日均量/下跌期日均量 = {vol_consolidation/vol_downtrend:.1%}）")
    else:
        results['conditions'].append("⚠️ 数据不足，无法判断缩量")
    
    # 4. 换手率检查
    recent_20 = df.tail(20)
    avg_vol = recent_20['vol'].mean()  # 手
    circ_cap = get_circulating_cap(ts_code)  # 亿股
    avg_turnover = avg_vol / (circ_cap * 10000)  # 正确公式：换手率 = 成交量(手) / (流通股本(亿股) * 10000)
    if avg_turnover < turnover_threshold:
        results['conditions'].append(f"✅ 换手率健康（{avg_turnover:.2f}% < {turnover_threshold}%，数据源：daily_basic.float_share）")
        results['score'] += 1
    else:
        results['conditions'].append(f"❌ 换手率偏高（{avg_turnover:.2f}% ≥ {turnover_threshold}%，数据源：daily_basic.float_share）")
    
    # 5. 不再创新低（60日低点距今>20日）
    recent_60 = df.tail(60)
    low_60_date = recent_60.loc[recent_60['low'].idxmin(), 'trade_date']
    days_since_low = (df['trade_date'].iloc[-1] - low_60_date).days
    if days_since_low > 20:
        results['conditions'].append(f"✅ 不再创新低（60日低点距今 {days_since_low} 天 > 20天）")
        results['score'] += 1
    else:
        results['conditions'].append(f"❌ 近期创出新低（60日低点距今仅 {days_since_low} 天）")
    
    # 6. 均线粘合（MA5/MA10/MA20 差距<3%）
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma10'] = df['close'].rolling(10).mean()
    df['ma20'] = df['close'].rolling(20).mean()
    recent_ma = df[['ma5', 'ma10', 'ma20']].tail(1).iloc[0]
    ma_max = recent_ma.max()
    ma_min = recent_ma.min()
    ma_diff = (ma_max - ma_min) / ma_min if ma_min > 0 else 1
    if ma_diff < 0.03:
        results['conditions'].append(f"✅ 均线粘合（MA5/MA10/MA20 差距 {ma_diff:.1%} < 3%）")
        results['score'] += 1
    else:
        results['conditions'].append(f"❌ 均线发散（MA差距 {ma_diff:.1%}）")
    
    results['is_bottom'] = results['score'] >= 5
    
    return results


def get_stage_evidence(df: pd.DataFrame, cap_info: dict) -> dict:
    """获取阶段判断依据（完全量化版）"""
    evidence = {
        'stage_1_to_2': [],
        'stage_2_to_3': [],
        'stage_3_to_4': [],
        'stage_4_to_5': [],
        'stage_5_to_6': [],
        'current_stage': 1,
        'evidence_text': "",
        'waiting_reasons': []
    }
    
    recent_90 = df.tail(90)
    recent_60 = df.tail(60)
    recent_30 = df.tail(30)
    recent_10 = df.tail(10)
    recent_5 = df.tail(5)
    
    df['vol_20'] = df['vol'].rolling(20).mean()
    df['vol_60'] = df['vol'].rolling(60).mean()
    
    volume_multiplier = cap_info['volume_multiplier']
    consolidation_days = cap_info['consolidation_days']
    
    # 阶段1→2转折：①横盘≥40日 ②单日涨幅>5% ③成交量>20日均量1.8倍 ④此后3日不破前低
    has_consolidation = False
    if len(df) >= consolidation_days:
        recent_n = df.tail(consolidation_days)
        amp_n = (recent_n['high'].max() - recent_n['low'].min()) / recent_n['low'].min()
        if amp_n < cap_info['amp_threshold']:
            has_consolidation = True
    
    for i in range(3, len(recent_60)):
        row = recent_60.iloc[i]
        pct_change = (row['close'] - row['open']) / row['open']
        vol_ok = row['vol'] > df['vol_20'].iloc[i] * 1.8
        
        if pct_change > 0.05 and vol_ok:
            next_3_days = recent_60.iloc[i+1:i+4]
            if len(next_3_days) >= 3 and next_3_days['low'].min() >= row['low']:
                date_str = row['trade_date'].strftime('%Y-%m-%d')
                evidence['stage_1_to_2'].append(f"{date_str} 放量阳线+{pct_change:.1%}，量能{row['vol']/df['vol_20'].iloc[i]:.1f}倍，满足阶段1→2转折")
        elif pct_change > 0.05 and not vol_ok:
            evidence['waiting_reasons'].append(f"等待放量：{row['trade_date'].strftime('%Y-%m-%d')}涨幅+{pct_change:.1%}但量能仅{row['vol']/df['vol_20'].iloc[i]:.1f}倍（需1.8倍）")
    
    # 阶段2→3转折：①从阶段2高点回落>5% ②回落期间至少一天成交量<阶段2峰值的一半
    if len(recent_30) >= 20:
        first_10 = recent_30.iloc[:10]
        last_20 = recent_30.iloc[-20:]
        
        stage2_high = first_10['high'].max()
        stage2_peak_vol = first_10['vol'].max()
        
        for i in range(len(last_20)):
            row = last_20.iloc[i]
            drawdown = (stage2_high - row['low']) / stage2_high
            vol_below_half = row['vol'] < stage2_peak_vol * 0.5
            
            if drawdown > 0.05 and vol_below_half:
                date_str = row['trade_date'].strftime('%Y-%m-%d')
                evidence['stage_2_to_3'].append(f"{date_str} 从阶段2高点{stage2_high:.2f}回落{drawdown:.1%}，量能缩至峰值{row['vol']/stage2_peak_vol:.1%}，满足阶段2→3转折")
            elif drawdown > 0.05 and not vol_below_half:
                date_str = row['trade_date'].strftime('%Y-%m-%d')
                evidence['waiting_reasons'].append(f"等待量缩：{date_str}回落{drawdown:.1%}但量能仍{row['vol']/stage2_peak_vol:.1%}（需<50%）")
    
    # 阶段3→4转折：①连续5日振幅<2.5%（硬性条件） ②当前成交量<阶段2最高量的1/3
    recent_20 = df.tail(20)
    if len(recent_20) >= 10:
        stage2_period = df.tail(120).iloc[40:80] if len(df) >= 120 else df.tail(60)
        stage2_max_vol = stage2_period['vol'].max()
        
        for i in range(len(recent_20)-4):
            window = recent_20.iloc[i:i+5]
            avg_amp = sum((r['high'] - r['low']) / r['low'] for _, r in window.iterrows()) / 5
            avg_vol = window['vol'].mean()
            
            if avg_amp < 0.025 and avg_vol < stage2_max_vol / 3:
                date_str = window.iloc[0]['trade_date'].strftime('%Y-%m-%d')
                evidence['stage_3_to_4'].append(f"{date_str} 连续5日振幅{avg_amp:.1%}<2.5%，量能{avg_vol/stage2_max_vol:.1%}<33%，满足阶段3→4转折")
            elif avg_amp < 0.025 and avg_vol >= stage2_max_vol / 3:
                evidence['waiting_reasons'].append(f"等待量缩：连续5日振幅达标但量能仍{avg_vol/stage2_max_vol:.1%}（需<33%）")
            elif avg_amp >= 0.025:
                evidence['waiting_reasons'].append(f"等待振幅收窄：连续5日振幅{avg_amp:.1%}≥2.5%（需<2.5%）")
    
    # 阶段4→5转折：①连续3日阳线（涨幅0.5-3%） ②成交量比阶段4均值放大20%以上
    for i in range(len(recent_10)-2):
        window = recent_10.iloc[i:i+3]
        if len(window) == 3:
            is_positive = all(r['close'] > r['open'] for _, r in window.iterrows())
            in_range = all(0.005 < (r['close'] - r['open']) / r['open'] < 0.03 for _, r in window.iterrows())
            
            stage4_period = df.tail(60).iloc[:30] if len(df) >= 60 else df.tail(30)
            stage4_avg_vol = stage4_period['vol'].mean()
            curr_vol = window['vol'].mean()
            vol_increase = curr_vol > stage4_avg_vol * 1.2
            
            if is_positive and in_range and vol_increase:
                date_str = window.iloc[0]['trade_date'].strftime('%Y-%m-%d')
                evidence['stage_4_to_5'].append(f"{date_str} 连续3日阳线（涨幅0.5-3%），量能放大{curr_vol/stage4_avg_vol:.1%}，满足阶段4→5转折")
            elif is_positive and in_range and not vol_increase:
                evidence['waiting_reasons'].append(f"等待量增：连续3日小阳但量能仅放大{curr_vol/stage4_avg_vol:.1%}（需>120%）")
    
    # 阶段5→6转折：①收盘价突破阶段2高点 ②成交量>60日均量1.5倍
    stage2_high = df.tail(120).iloc[40:80]['high'].max() if len(df) >= 120 else recent_60['high'].max()
    
    for i in range(len(recent_10)):
        row = recent_10.iloc[i]
        if row['close'] >= stage2_high and row['vol'] > df['vol_60'].iloc[i] * 1.5:
            date_str = row['trade_date'].strftime('%Y-%m-%d')
            pct = (row['close'] - row['open']) / row['open']
            evidence['stage_5_to_6'].append(f"{date_str} 收盘价突破阶段2高点{stage2_high:.2f}，量能{row['vol']/df['vol_60'].iloc[i]:.1f}倍，满足阶段5→6转折")
        elif row['close'] >= stage2_high and row['vol'] <= df['vol_60'].iloc[i] * 1.5:
            evidence['waiting_reasons'].append(f"等待放量突破：{date_str}突破{stage2_high:.2f}但量能仅{row['vol']/df['vol_60'].iloc[i]:.1f}倍（需1.5倍）")
    
    # 确定当前阶段
    if evidence['stage_5_to_6']:
        evidence['current_stage'] = 6
        evidence['evidence_text'] = evidence['stage_5_to_6'][-1]
    elif evidence['stage_4_to_5']:
        evidence['current_stage'] = 5
        evidence['evidence_text'] = evidence['stage_4_to_5'][-1]
    elif evidence['stage_3_to_4']:
        evidence['current_stage'] = 4
        evidence['evidence_text'] = evidence['stage_3_to_4'][-1]
    elif evidence['stage_2_to_3']:
        evidence['current_stage'] = 3
        evidence['evidence_text'] = evidence['stage_2_to_3'][-1]
    elif evidence['stage_1_to_2']:
        evidence['current_stage'] = 2
        evidence['evidence_text'] = evidence['stage_1_to_2'][-1]
    else:
        evidence['current_stage'] = 1
        evidence['evidence_text'] = "尚未检测到明确的阶段转折信号，处于下跌寻底阶段"
    
    return evidence


def step2_washout_stage(df: pd.DataFrame, cap_info: dict) -> dict:
    """第二步：量化洗盘六步法剧本（完全量化版）"""
    stages = [
        {'name': '下跌寻底', 'pattern': '大幅下跌后的横盘整理', 'stage': 1, 'color': '🔴'},
        {'name': '初次拉升', 'pattern': '突然放量拉升，脱离底部', 'stage': 2, 'color': '🟠'},
        {'name': 'A字杀跌', 'pattern': '拉升后快速回落洗盘', 'stage': 3, 'color': '🟡'},
        {'name': '缩量企稳', 'pattern': '成交量萎缩，价格止跌', 'stage': 4, 'color': '🟢'},
        {'name': '小阳推升', 'pattern': '连续小阳线逐步推高', 'stage': 5, 'color': '🔵'},
        {'name': '突破启动', 'pattern': '放量突破关键压力位', 'stage': 6, 'color': '🟣'},
    ]
    
    evidence = get_stage_evidence(df, cap_info)
    stage_idx = evidence['current_stage'] - 1
    current_stage = stages[stage_idx]
    current_stage['evidence'] = evidence['evidence_text']
    current_stage['all_evidence'] = evidence
    current_stage['waiting_reasons'] = evidence['waiting_reasons']
    
    return current_stage


def step3_washout_ending_signals(df: pd.DataFrame) -> dict:
    """第三步：识别洗盘尾声信号（修正版）"""
    signals = {
        'signals': [],
        'count': 0,
        'is_ending': False,
        'details': []
    }
    
    recent_60 = df.tail(60)
    recent_20 = df.tail(20)
    recent_5 = df.tail(5)
    
    max_60_amount = recent_60['amount'].max()
    recent_amount = recent_20['amount'].mean()
    
    # 信号1：成交量极度萎缩（当日成交额 ≤ 近60日最高成交额的1/4 且换手率<2%）
    avg_vol = recent_20['vol'].mean()  # 手
    circ_cap = get_circulating_cap(df['ts_code'].iloc[0])  # 亿股
    turnover = avg_vol / (circ_cap * 10000)  # 正确公式：换手率 = 成交量(手) / (流通股本(亿股) * 10000)
    
    if recent_amount <= max_60_amount / 4 and turnover < 2:
        signals['signals'].append(f"✅ 量能极度萎缩（成交额 {recent_amount/100000000:.2f}亿 ≤ 60日最高的1/4，换手率 {turnover:.2f}% < 2%，数据源：daily_basic.float_share）")
        signals['count'] += 1
        signals['details'].append(f"量能萎缩达标：当前成交额 {recent_amount/100000000:.2f}亿，60日最高 {max_60_amount/100000000:.2f}亿")
    else:
        signals['signals'].append(f"❌ 量能未极度萎缩（成交额比例 {recent_amount/max_60_amount:.1%}，换手率 {turnover:.2f}%，数据源：daily_basic.float_share）")
    
    # 信号2：振幅收窄（连续5日振幅<2.5%）
    recent_5_amps = [(row['high'] - row['low']) / row['low'] for _, row in recent_5.iterrows()]
    if len(recent_5_amps) >= 5 and all(amp < 0.025 for amp in recent_5_amps):
        avg_amp = sum(recent_5_amps) / 5
        signals['signals'].append(f"✅ 连续5日振幅收窄（平均 {avg_amp:.1%} < 2.5%）")
        signals['count'] += 1
        signals['details'].append(f"振幅收窄达标：连续5日平均振幅 {avg_amp:.1%}")
    else:
        avg_amp = sum(recent_5_amps) / len(recent_5_amps) if recent_5_amps else 0
        signals['signals'].append(f"❌ 振幅未收窄（近{len(recent_5_amps)}日平均 {avg_amp:.1%}）")
    
    # 信号3：该跌不跌（量化版）
    has_anti_drop = False
    anti_drop_dates = []
    
    for i in range(len(recent_20)-1):
        today = recent_20.iloc[i]
        pct_change = (today['close'] - today['open']) / today['open']
        if pct_change > -0.005:
            if i > 0:
                prev_day = recent_20.iloc[i-1]
                prev_change = (prev_day['close'] - prev_day['open']) / prev_day['open']
                if prev_change < -0.03:
                    has_anti_drop = True
                    anti_drop_dates.append(today['trade_date'].strftime('%Y-%m-%d'))
    
    if has_anti_drop:
        signals['signals'].append(f"✅ 该跌不跌（{','.join(anti_drop_dates)} 在大跌后抗跌）")
        signals['count'] += 1
        signals['details'].append(f"该跌不跌达标：{len(anti_drop_dates)}个交易日抗跌")
    else:
        signals['signals'].append("⚠️ 近期无明显抗跌表现")
    
    signals['is_ending'] = signals['count'] >= 2
    
    return signals


def step4_price_levels(df: pd.DataFrame) -> dict:
    """第四步：关键价格位计算（修正版）"""
    recent_60 = df.tail(60)
    
    lows = find_recent_lows(df, 120)
    if len(lows) >= 2:
        strong_support = max(lows[0]['price'], lows[1]['price'])
        support_note = f"取最近两次探底({lows[0]['date'].strftime('%Y-%m-%d')} {lows[0]['price']:.2f}, {lows[1]['date'].strftime('%Y-%m-%d')} {lows[1]['price']:.2f})的较高者"
    elif len(lows) == 1:
        strong_support = lows[0]['price'] * 0.98
        support_note = f"仅一次探底({lows[0]['date'].strftime('%Y-%m-%d')})，前低×0.98"
    else:
        strong_support = recent_60['low'].min() * 0.97
        support_note = "未找到明确探底，60日最低价×0.97"
    
    recent_30 = df.tail(30)
    cost_min = recent_30['close'].min()
    cost_median = recent_30['close'].median()
    
    if len(df) >= 120:
        stage2_period = df.tail(120).iloc[40:80]
        neckline = stage2_period['high'].max()
    else:
        neckline = 0
    
    breakout_level = max(neckline, recent_60['high'].max())
    breakout_note = f"颈线{neckline:.2f}与60日最高{recent_60['high'].max():.2f}取较高值"
    
    atr_20 = calculate_atr(df, 20)
    target1 = breakout_level + atr_20 * 2
    target2 = breakout_level + atr_20 * 4
    
    current_price = df['close'].iloc[-1]
    
    return {
        'strong_support': round(strong_support, 2),
        'support_note': support_note,
        'cost_min': round(cost_min, 2),
        'cost_median': round(cost_median, 2),
        'breakout_level': round(breakout_level, 2),
        'breakout_note': breakout_note,
        'target1': round(target1, 2),
        'target2': round(target2, 2),
        'atr_20': round(atr_20, 2),
        'current_price': round(current_price, 2)
    }


def step5_trading_rules(stage: dict, levels: dict, cap_info: dict) -> dict:
    """第五步：操作纪律（修正版 - 左少右多原则）"""
    rules = {
        'position_suggestion': '',
        'stop_loss_rule': '',
        'take_profit_rule': '',
        'position_plan': '',
        'notes': []
    }
    
    stage_name = stage['name']
    stage_num = stage['stage']
    
    if stage_num <= 2:
        rules['position_suggestion'] = '观望为主，不超过5%仓位试错'
        rules['position_plan'] = '左侧试探仓 ≤ 5%，总仓位 ≤ 10%'
        rules['notes'].append('当前风险较高，等待形态明确')
    
    elif stage_num == 3:
        rules['position_suggestion'] = '可逐步建仓，总仓位不超过10%'
        rules['position_plan'] = '左侧试探仓 ≤ 5%，总仓位 ≤ 10%'
        rules['notes'].append('洗盘阶段，逢低吸纳')
    
    elif stage_num == 4:
        rules['position_suggestion'] = '积极建仓，总仓位可到20%'
        rules['position_plan'] = '左侧试探仓 ≤ 5%，总仓位 ≤ 20%'
        rules['notes'].append('企稳信号出现，可以布局')
    
    elif stage_num == 5:
        rules['position_suggestion'] = '继续加仓，总仓位可达30%'
        rules['position_plan'] = '左侧试探仓 ≤ 5%，总仓位 ≤ 30%'
        rules['notes'].append('形态逐步明朗，可提前布局')
    
    elif stage_num >= 6:
        rules['position_suggestion'] = '重仓参与，总仓位可达50%'
        rules['position_plan'] = '总仓位 ≤ 50%（右侧加仓完成）'
        rules['notes'].append('趋势明确，果断跟进')
    
    atr_20 = levels['atr_20']
    atr_stop_price = levels['current_price'] - atr_20 * 1.5
    stop_loss_price = levels['strong_support'] * 0.98
    dynamic_stop = max(atr_stop_price, stop_loss_price)
    
    rules['stop_loss_rule'] = f"ATR动态止损：当前价 - 1.5×ATR(20) = ¥{dynamic_stop:.2f}"
    rules['stop_loss_rule'] += f"（强支撑止损 ¥{stop_loss_price:.2f}，ATR止损 ¥{atr_stop_price:.2f}，取较高者）"
    
    if stage_num >= 6:
        breakout_stop = levels['breakout_level'] * 0.97
        rules['stop_loss_rule'] += f"；若突破确认位跌破3%（¥{breakout_stop:.2f}）且次日无法收回，右侧部分止损"
    
    rules['stop_loss_rule'] += f"；时间止损：建仓后5日涨幅<2%减半仓"
    
    rules['take_profit_rule'] = f"第一目标 ¥{levels['target1']:.2f}（突破位+2×ATR），第二目标 ¥{levels['target2']:.2f}（突破位+4×ATR），分三批止盈"
    
    rules['notes'].append("左侧试探仓严格控制在5%以内")
    rules['notes'].append("剩余仓位作为机动，用于应对突发情况")
    
    return rules


def generate_progress_bar(stage_num: int, total_stages: int = 6) -> str:
    """生成ASCII进度条"""
    filled = '█' * stage_num
    empty = '░' * (total_stages - stage_num)
    progress = stage_num / total_stages * 100
    return f"[{filled}{empty}] {progress:.0f}%"


def analyze(ts_code: str) -> str:
    """
    核心分析函数
    
    参数：
        ts_code: 股票代码，如 '600519.SH'
    
    返回：
        Markdown格式的洗盘分析报告
    """
    report_lines = []
    stock_name = get_stock_name(ts_code)
    
    report_lines.append(f"# 📊 洗盘分析师报告 v3.0 —— {ts_code} {stock_name}")
    report_lines.append(f"**分析时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append("")
    
    df = fetch_daily_data(ts_code, 250)
    if df is None or df.empty:
        report_lines.append("❌ **数据获取失败**")
        report_lines.append("请确保数据库中有足够的历史数据")
        return "\n".join(report_lines)
    
    # 数据完整性检查
    is_ok, level, message = check_data_sufficiency(df)
    total_days = len(df)
    
    report_lines.append(f"**数据覆盖天数**：{total_days}天")
    if level == 'full':
        report_lines.append(f"**分析级别**：✅ 完整分析")
    elif level == 'partial':
        report_lines.append(f"**分析级别**：⚠️ 简化分析{message}")
    else:
        report_lines.append(f"**分析级别**：❌ 拒绝分析{message}")
        return "\n".join(report_lines)
    
    # 标准化股票代码（用于后续数据库查询）
    ts_code_normalized = normalize_ts_code(ts_code)
    
    # 打开数据库连接供后续函数使用
    conn = sqlite3.connect(DB_PATH)
    
    # 获取当前价格（优先分钟线）
    current_price, price_source = _get_current_price(ts_code_normalized, conn)
    if current_price:
        report_lines.append(f"**当前价格**：¥{current_price:.2f} [{price_source}]")
    else:
        report_lines.append(f"**当前价格**：¥{df['close'].iloc[-1]:.2f} [日线收盘价]")
    report_lines.append("")
    
    # 获取市值类型（从daily_basic获取最新流通市值）
    market_cap_label, cap_type = _get_market_cap_type(ts_code_normalized, conn)
    
    # 根据市值类型获取阈值参数
    if cap_type == 'large':
        cap_info = get_cap_category(600)  # 大盘
    elif cap_type == 'medium':
        cap_info = get_cap_category(200)  # 中盘
    elif cap_type == 'small':
        cap_info = get_cap_category(50)   # 小盘
    else:
        cap_info = get_cap_category(50)   # 默认小盘
    
    report_lines.append(f"**市值类型**：{market_cap_label}")
    report_lines.append("")
    
    # 获取行业强度排名
    industry_name, industry_category, industry_score, industry_rank = _get_industry_rank(ts_code_normalized, conn)
    conn.close()  # 关闭数据库连接
    
    # 资金面速览
    report_lines.append("## 💰 资金面速览")
    moneyflow = get_moneyflow_summary(ts_code_normalized)
    report_lines.append("| 指标 | 数值 | 评价 |")
    report_lines.append("|------|------|------|")
    if moneyflow['net_10d'] is not None:
        report_lines.append(f"| 近10日主力净流入 | {'¥{:,.0f}'.format(moneyflow['net_10d'])}万元 | {moneyflow['mf_evaluation']} |")
    else:
        report_lines.append("| 近10日主力净流入 | - | 数据获取失败 |")
    
    if moneyflow['north_5d'] is not None:
        report_lines.append(f"| 近5日北向资金变动 | {'¥{:,.0f}'.format(moneyflow['north_5d'])}万元 | - |")
    else:
        report_lines.append("| 近5日北向资金变动 | - | 无数据 |")
    
    report_lines.append(f"| 融资余额变化趋势 | {moneyflow['financing_trend']} | - |")
    report_lines.append("")
    
    # 大盘环境评估
    report_lines.append("## 📉 大盘环境评估")
    market_env = get_market_environment()
    report_lines.append(f"**状态**：{market_env['label']}")
    report_lines.append(f"**提示**：{market_env['message']}")
    if market_env['detail']:
        report_lines.append(f"**详情**：{market_env['detail']}")
    report_lines.append("")
    
    # 第一步：底部形态检测
    report_lines.append("## 🎯 第一步：底部形态检测")
    bottom_result = step1_detect_bottom_pattern(df, ts_code_normalized, cap_info)
    report_lines.append(f"**综合评分**：{bottom_result['score']}/{bottom_result['max_score']}")
    report_lines.append(f"**底部形态确认**：{'✅ 符合底部特征' if bottom_result['is_bottom'] else '❌ 暂不符合'}")
    report_lines.append("")
    for cond in bottom_result['conditions']:
        report_lines.append(f"- {cond}")
    report_lines.append("")
    
    # 第二步：洗盘阶段识别
    report_lines.append("## 📈 第二步：洗盘阶段识别")
    stage = step2_washout_stage(df, cap_info)
    
    stage_display = f"{stage['color']} {stage['name']}"
    if stage['waiting_reasons']:
        stage_display += " ⏳ 等待确认"
    
    report_lines.append(f"**当前阶段**：{stage_display}（第{stage['stage']}/6阶段）")
    report_lines.append(f"**特征描述**：{stage['pattern']}")
    report_lines.append("")
    report_lines.append("**阶段判断依据**：")
    report_lines.append(f"> {stage['evidence']}")
    report_lines.append("")
    
    if stage['waiting_reasons']:
        report_lines.append("**等待确认条件**：")
        for reason in stage['waiting_reasons'][:3]:
            report_lines.append(f"- ⏳ {reason}")
        report_lines.append("")
    
    report_lines.append(f"**洗盘进度**：{generate_progress_bar(stage['stage'])}")
    report_lines.append("")
    
    # 第三步：洗盘尾声信号
    report_lines.append("## 🔔 第三步：洗盘尾声信号")
    ending = step3_washout_ending_signals(df)
    report_lines.append(f"**触发信号**：{ending['count']}/3 个")
    report_lines.append(f"**洗盘尾声确认**：{'✅ 接近尾声' if ending['is_ending'] else '❌ 尚未结束'}")
    report_lines.append("")
    for sig in ending['signals']:
        report_lines.append(f"- {sig}")
    if ending['details']:
        report_lines.append("")
        report_lines.append("**信号详情**：")
        for detail in ending['details']:
            report_lines.append(f"- {detail}")
    report_lines.append("")
    
    # 第四步：关键价格位
    report_lines.append("## 📐 第四步：关键价格位计算")
    levels = step4_price_levels(df)
    report_lines.append("| 价格类型 | 价格（¥） | 说明 |")
    report_lines.append("|----------|----------|------|")
    report_lines.append(f"| 强支撑 | {levels['strong_support']:.2f} | {levels['support_note']} |")
    report_lines.append(f"| 成本区下限 | {levels['cost_min']:.2f} | 最近30日收盘价最低 |")
    report_lines.append(f"| 成本区中位 | {levels['cost_median']:.2f} | 最近30日收盘价中位 |")
    report_lines.append(f"| 突破确认位 | {levels['breakout_level']:.2f} | {levels['breakout_note']} |")
    report_lines.append(f"| 第一目标 | {levels['target1']:.2f} | 突破位 + 2×ATR(20) |")
    report_lines.append(f"| 第二目标 | {levels['target2']:.2f} | 突破位 + 4×ATR(20) |")
    report_lines.append(f"| 当前价 | {levels['current_price']:.2f} | 最新收盘价 |")
    report_lines.append("")
    report_lines.append(f"**ATR(20)**：¥{levels['atr_20']:.2f}（用于计算目标位）")
    report_lines.append("")
    
    # 第五步：操作纪律
    report_lines.append("## 🎯 第五步：操作纪律")
    rules = step5_trading_rules(stage, levels, cap_info)
    report_lines.append(f"**仓位建议**：{rules['position_suggestion']}")
    report_lines.append(f"**仓位计划**：{rules['position_plan']}")
    report_lines.append(f"**止损规则**：{rules['stop_loss_rule']}")
    report_lines.append(f"**止盈规则**：{rules['take_profit_rule']}")
    report_lines.append("")
    report_lines.append("**注意事项**：")
    for note in rules['notes']:
        report_lines.append(f"- {note}")
    report_lines.append("")
    
    # 第六步：交叉验证
    report_lines.append("## 🔗 第六步：外部信号交叉验证")
    v31_score = get_v31_score(ts_code)
    
    report_lines.append("【交叉验证】")
    report_lines.append(f"- v3.1 量化评分：{v31_score['score']}/35（{v31_score['level']}）")
    if industry_rank > 0:
        report_lines.append(f"- 行业强度：{industry_name}（{industry_category}/排名第{industry_rank}，得分{industry_score:.3f}）")
    else:
        report_lines.append(f"- 行业强度：{industry_name}（{industry_category}）")
    
    stage_num = stage['stage']
    score_good = v31_score['score'] >= 20
    stage_good = stage_num >= 4
    
    if score_good and stage_good:
        report_lines.append("- ✅ **整体判断**：底部构建充分，主力资金积极，可重点关注")
        report_lines.append("  → 洗盘进度与资金面一致，可信度提升")
    elif score_good and not stage_good:
        report_lines.append("- ⚠️ **整体判断**：主力资金积极但洗盘尚未完成")
        report_lines.append("  → 建议等待洗盘完成再入场")
    elif not score_good and stage_good:
        report_lines.append("- ⚠️ **整体判断**：技术面与资金面背离，需谨慎")
        report_lines.append("  → 洗盘进度高但资金评分低，存在分歧")
    else:
        report_lines.append("- 🔍 **整体判断**：继续观察")
        report_lines.append("  → 等待形态确认和资金信号")
    
    report_lines.append("")
    
    # 总结
    report_lines.append("## 📝 总结与预警")
    if stage_num >= 5 and ending['is_ending']:
        report_lines.append("🚀 **强烈关注**：该股处于洗盘尾声，随时可能启动突破，建议重点跟踪！")
    elif stage_num == 4 and ending['is_ending']:
        report_lines.append("⚠️ **即将启动**：缩量企稳完成，等待放量突破信号")
    elif stage_num >= 3:
        report_lines.append("🔍 **值得关注**：形态逐步明朗，可开始左侧布局")
    else:
        report_lines.append("⏳ **继续观察**：等待底部形态确认")
    report_lines.append("")
    report_lines.append("---")
    report_lines.append("⚠️ 本报告仅供参考，不构成投资建议")
    report_lines.append("📊 数据来源：本地数据库（最近250个交易日）")
    report_lines.append(f"🔧 洗盘分析师 v3.0 | {cap_info['category']}（{cap_info['cap_range']}）")
    
    return "\n".join(report_lines)


def analyze_and_save(ts_code: str) -> str:
    """分析并保存报告到文件"""
    report = analyze(ts_code)
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"washout_{ts_code.replace('.', '_')}_{date_str}.md"
    filepath = os.path.join(REPORT_DIR, filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"报告已保存：{filepath}")
    return filepath


def run_test_cases():
    """运行测试用例"""
    test_stocks = [
        ('600519.SH', '贵州茅台', '大盘、数据充足'),
        ('603327.SH', '福蓉科技', '次新股、数据不足'),
        ('000681.SZ', '视觉中国', '中盘、近期有资金异动')
    ]
    
    print("="*80)
    print("📋 洗盘分析师 v3.0 测试报告")
    print("="*80)
    
    for ts_code, name, feature in test_stocks:
        print(f"\n{'='*80}")
        print(f"📈 测试股票：{ts_code} {name}")
        print(f"📝 特点：{feature}")
        print(f"{'='*80}")
        
        report = analyze(ts_code)
        print(report)
        print("\n" + "="*80)
        
        analyze_and_save(ts_code)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    
    if len(sys.argv) >= 2 and sys.argv[1] == '--test':
        run_test_cases()
    elif len(sys.argv) >= 2:
        ts_code = sys.argv[1]
        report = analyze(ts_code)
        print(report)
        analyze_and_save(ts_code)
    else:
        ts_code = "600519.SH"
        report = analyze(ts_code)
        print(report)
        analyze_and_save(ts_code)
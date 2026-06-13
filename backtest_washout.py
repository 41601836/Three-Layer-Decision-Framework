import sqlite3
import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = "db/stock_daily.db"
REPORT_DIR = "reports"

# 导入洗盘分析核心函数
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from washout_analyst import (
    fetch_daily_data, 
    step1_detect_bottom_pattern, 
    step2_washout_stage, 
    step4_price_levels,
    get_cap_category,
    normalize_ts_code
)

def get_stock_list():
    """获取股票池：剔除ST、上市不足250天的新股"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT ts_code, name, list_date
        FROM stock_list
        WHERE name NOT LIKE '%ST%' 
            AND list_date < '20240601'  -- 上市超过1年
    """)
    
    stocks = []
    for row in cursor.fetchall():
        stocks.append({
            'ts_code': row[0],
            'name': row[1],
            'list_date': row[2]
        })
    
    conn.close()
    return stocks

def get_trading_dates(start_date, end_date):
    """获取指定区间内的交易日"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT DISTINCT trade_date
        FROM daily_prices
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY trade_date
    """, (start_date, end_date))
    
    dates = [row[0] for row in cursor.fetchall()]
    conn.close()
    return dates

def get_index_data(ts_code, start_date, end_date):
    """获取指数数据"""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        """SELECT trade_date, close FROM daily_prices 
           WHERE ts_code = ? AND trade_date BETWEEN ? AND ?
           ORDER BY trade_date""",
        conn,
        params=(ts_code, start_date, end_date)
    )
    conn.close()
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df.set_index('trade_date', inplace=True)
    return df

def run_backtest():
    """运行回测主函数"""
    start_date = '20250601'
    end_date = '20251231'
    
    print("=== 洗盘分析师策略回测 ===")
    print(f"回测区间: {start_date} ~ {end_date}")
    print("正在初始化...")
    
    # 获取交易日列表
    trading_dates = get_trading_dates(start_date, end_date)
    print(f"交易日数量: {len(trading_dates)}")
    
    # 获取股票池
    stock_list = get_stock_list()
    print(f"股票池数量: {len(stock_list)}")
    
    # 获取沪深300指数数据
    hs300_df = get_index_data('000300.SH', start_date, end_date)
    
    # 回测状态
    positions = {}  # 持仓: {ts_code: {entry_date, entry_price, target1, target2, stop_price, shares}}
    trade_history = []  # 交易记录
    daily_equity = []  # 每日权益曲线
    max_position_size = 5  # 最大持仓数量
    position_weight = 0.1  # 单只仓位10%
    
    # 初始资金
    initial_capital = 1000000
    current_capital = initial_capital
    available_cash = initial_capital
    
    # 用于存储每日数据
    daily_data_cache = {}
    
    print("\n开始回测...")
    for i, date in enumerate(trading_dates):
        if i % 20 == 0:
            print(f"进度: {i+1}/{len(trading_dates)} ({(i+1)/len(trading_dates):.1%})")
        
        # 获取下一个交易日的开盘价（用于买入）
        next_date = trading_dates[i+1] if i < len(trading_dates)-1 else date
        
        # 每日更新：检查止盈止损
        positions_to_close = []
        for ts_code, pos in positions.items():
            # 获取当日数据
            if ts_code not in daily_data_cache:
                daily_data_cache[ts_code] = fetch_daily_data(ts_code, 300)
            
            df = daily_data_cache[ts_code]
            if df is None or df.empty:
                positions_to_close.append(ts_code)
                continue
            
            # 找到当日数据
            day_data = df[df['trade_date'] == pd.to_datetime(date)]
            if day_data.empty:
                continue
            
            close_price = day_data['close'].iloc[0]
            pos['current_price'] = close_price
            pos['holding_days'] += 1
            
            # 检查止损
            if close_price <= pos['stop_price']:
                trade_history.append({
                    'ts_code': ts_code,
                    'entry_date': pos['entry_date'],
                    'exit_date': date,
                    'entry_price': pos['entry_price'],
                    'exit_price': close_price,  # 次日开盘价，这里简化为当日收盘价
                    'reason': '止损',
                    'holding_days': pos['holding_days'],
                    'profit': (close_price - pos['entry_price']) / pos['entry_price']
                })
                available_cash += close_price * pos['shares']
                positions_to_close.append(ts_code)
                continue
            
            # 检查止盈第一目标
            if not pos['target1_hit'] and close_price >= pos['target1']:
                # 卖出50%
                sell_shares = pos['shares'] // 2
                available_cash += close_price * sell_shares
                pos['shares'] -= sell_shares
                pos['target1_hit'] = True
                
                trade_history.append({
                    'ts_code': ts_code,
                    'entry_date': pos['entry_date'],
                    'exit_date': date,
                    'entry_price': pos['entry_price'],
                    'exit_price': close_price,
                    'reason': '止盈50%',
                    'holding_days': pos['holding_days'],
                    'profit': (close_price - pos['entry_price']) / pos['entry_price']
                })
                continue
            
            # 检查止盈第二目标
            if pos['target1_hit'] and close_price >= pos['target2']:
                trade_history.append({
                    'ts_code': ts_code,
                    'entry_date': pos['entry_date'],
                    'exit_date': date,
                    'entry_price': pos['entry_price'],
                    'exit_price': close_price,
                    'reason': '止盈100%',
                    'holding_days': pos['holding_days'],
                    'profit': (close_price - pos['entry_price']) / pos['entry_price']
                })
                available_cash += close_price * pos['shares']
                positions_to_close.append(ts_code)
                continue
            
            # 检查强制平仓（20天）
            if pos['holding_days'] >= 20:
                trade_history.append({
                    'ts_code': ts_code,
                    'entry_date': pos['entry_date'],
                    'exit_date': date,
                    'entry_price': pos['entry_price'],
                    'exit_price': close_price,
                    'reason': '强制平仓',
                    'holding_days': pos['holding_days'],
                    'profit': (close_price - pos['entry_price']) / pos['entry_price']
                })
                available_cash += close_price * pos['shares']
                positions_to_close.append(ts_code)
                continue
        
        # 执行平仓
        for ts_code in positions_to_close:
            del positions[ts_code]
        
        # 寻找买入信号
        if len(positions) < max_position_size:
            # 只检查部分股票以提高速度
            check_count = min(50, len(stock_list))
            
            for stock in stock_list[:check_count]:
                ts_code = stock['ts_code']
                
                # 跳过已持仓
                if ts_code in positions:
                    continue
                
                # 获取数据
                if ts_code not in daily_data_cache:
                    daily_data_cache[ts_code] = fetch_daily_data(ts_code, 300)
                
                df = daily_data_cache[ts_code]
                if df is None or len(df) < 250:
                    continue
                
                # 确保数据包含当日
                if df['trade_date'].max().date() < pd.to_datetime(date).date():
                    continue
                
                # 获取市值类型（简化处理）
                cap_info = get_cap_category(200)  # 默认中盘
                
                # 底部形态检测
                bottom_result = step1_detect_bottom_pattern(df, ts_code, cap_info)
                
                # 洗盘阶段识别
                stage = step2_washout_stage(df, cap_info)
                
                # 价格水平计算
                price_levels = step4_price_levels(df)
                
                # 获取当日收盘价
                today_df = df[df['trade_date'] == pd.to_datetime(date)]
                if today_df.empty:
                    continue
                today_close = today_df['close'].iloc[0]
                
                # 信号条件
                condition1 = bottom_result['score'] >= 4  # 底部形态评分 >= 4
                condition2 = stage['stage'] in [4, 5]  # 阶段4或阶段5
                condition3 = (today_close >= price_levels['cost_min'] and 
                            today_close <= price_levels['cost_median'])  # 在成本区下限与中位之间
                
                if condition1 and condition2 and condition3:
                    # 获取次日开盘价
                    next_df = df[df['trade_date'] == pd.to_datetime(next_date)]
                    if next_df.empty:
                        continue
                    entry_price = next_df['open'].iloc[0]
                    
                    # 计算可买数量
                    position_value = available_cash * position_weight / (max_position_size - len(positions))
                    shares = int(position_value / entry_price)
                    
                    if shares > 0 and available_cash >= shares * entry_price:
                        available_cash -= shares * entry_price
                        positions[ts_code] = {
                            'entry_date': next_date,
                            'entry_price': entry_price,
                            'target1': price_levels['target1'],
                            'target2': price_levels['target2'],
                            'stop_price': price_levels['strong_support'] * 0.98,  # 强支撑下方2%
                            'shares': shares,
                            'holding_days': 0,
                            'target1_hit': False,
                            'current_price': entry_price
                        }
        
        # 计算当日权益
        positions_value = sum(pos['current_price'] * pos['shares'] for pos in positions.values()) if positions else 0
        total_equity = available_cash + positions_value
        daily_equity.append({
            'date': date,
            'equity': total_equity,
            'cash': available_cash,
            'positions': positions_value,
            'position_count': len(positions)
        })
    
    print("\n回测完成！")
    return trade_history, daily_equity, hs300_df

def calculate_metrics(trade_history, daily_equity, initial_capital):
    """计算绩效指标"""
    if not trade_history:
        return {}
    
    metrics = {}
    
    # 总交易笔数
    metrics['total_trades'] = len(trade_history)
    
    # 胜率
    wins = sum(1 for t in trade_history if t['profit'] > 0)
    metrics['win_rate'] = wins / len(trade_history)
    
    # 平均盈利/亏损
    profits = [t['profit'] for t in trade_history if t['profit'] > 0]
    losses = [t['profit'] for t in trade_history if t['profit'] <= 0]
    metrics['avg_win'] = np.mean(profits) if profits else 0
    metrics['avg_loss'] = np.mean(losses) if losses else 0
    
    # 盈亏比
    metrics['profit_ratio'] = abs(metrics['avg_win'] / metrics['avg_loss']) if metrics['avg_loss'] != 0 else float('inf')
    
    # 总收益
    final_equity = daily_equity[-1]['equity']
    metrics['total_return'] = (final_equity - initial_capital) / initial_capital
    
    # 最大回撤
    equity_values = [d['equity'] for d in daily_equity]
    peak = equity_values[0]
    max_drawdown = 0
    for eq in equity_values:
        if eq > peak:
            peak = eq
        drawdown = (peak - eq) / peak
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    metrics['max_drawdown'] = max_drawdown
    
    # 持仓周期分布
    holding_days = [t['holding_days'] for t in trade_history]
    metrics['avg_holding_days'] = np.mean(holding_days)
    metrics['max_holding_days'] = max(holding_days)
    metrics['min_holding_days'] = min(holding_days)
    
    # 按月份统计
    monthly_stats = defaultdict(lambda: {'trades': 0, 'wins': 0, 'profit': 0})
    for trade in trade_history:
        month = trade['entry_date'][:6]
        monthly_stats[month]['trades'] += 1
        monthly_stats[month]['wins'] += 1 if trade['profit'] > 0 else 0
        monthly_stats[month]['profit'] += trade['profit']
    
    metrics['monthly_stats'] = dict(monthly_stats)
    
    return metrics

def generate_report(trade_history, daily_equity, hs300_df, metrics):
    """生成回测报告"""
    report_lines = []
    
    report_lines.append("# 📊 洗盘分析师策略回测报告 (2025年H2)")
    report_lines.append("")
    report_lines.append("## 📋 策略核心参数")
    report_lines.append("| 参数 | 值 |")
    report_lines.append("|------|------|")
    report_lines.append("| 回测区间 | 2025-06-01 ~ 2025-12-31 |")
    report_lines.append("| 初始资金 | ¥1,000,000 |")
    report_lines.append("| 单只仓位 | 10% |")
    report_lines.append("| 最大持仓 | 5只 |")
    report_lines.append("| 止损规则 | 跌破强支撑价 |")
    report_lines.append("| 止盈规则 | 第一目标卖50%，第二目标卖完 |")
    report_lines.append("| 强制平仓 | 持仓超20天 |")
    report_lines.append("")
    
    report_lines.append("## 📈 绩效汇总")
    report_lines.append("| 指标 | 数值 |")
    report_lines.append("|------|------|")
    report_lines.append(f"| 总交易笔数 | {metrics.get('total_trades', 0)} |")
    report_lines.append(f"| 胜率 | {metrics.get('win_rate', 0):.1%} |")
    report_lines.append(f"| 平均盈利 | {metrics.get('avg_win', 0):.1%} |")
    report_lines.append(f"| 平均亏损 | {metrics.get('avg_loss', 0):.1%} |")
    report_lines.append(f"| 盈亏比 | {metrics.get('profit_ratio', 0):.2f} |")
    report_lines.append(f"| 总收益率 | {metrics.get('total_return', 0):.1%} |")
    report_lines.append(f"| 最大回撤 | {metrics.get('max_drawdown', 0):.1%} |")
    report_lines.append(f"| 平均持仓天数 | {metrics.get('avg_holding_days', 0):.1f} |")
    report_lines.append("")
    
    report_lines.append("## 📅 月度统计")
    report_lines.append("| 月份 | 交易笔数 | 胜率 | 盈亏比 |")
    report_lines.append("|------|----------|------|--------|")
    for month, stats in sorted(metrics.get('monthly_stats', {}).items()):
        win_rate = stats['wins'] / stats['trades'] if stats['trades'] > 0 else 0
        report_lines.append(f"| {month} | {stats['trades']} | {win_rate:.1%} | {stats['profit']:.1%} |")
    report_lines.append("")
    
    report_lines.append("## 📉 收益曲线分析")
    report_lines.append("- **期初权益**: ¥1,000,000")
    report_lines.append(f"- **期末权益**: ¥{daily_equity[-1]['equity']:,.0f}")
    report_lines.append(f"- **累计收益**: {metrics.get('total_return', 0):.1%}")
    report_lines.append(f"- **最大回撤**: {metrics.get('max_drawdown', 0):.1%}")
    report_lines.append("")
    
    report_lines.append("## 🔍 典型案例分析")
    if trade_history:
        # 最佳交易
        best_trade = max(trade_history, key=lambda x: x['profit'])
        report_lines.append("### ✅ 最佳交易")
        report_lines.append(f"- **股票**: {best_trade['ts_code']}")
        report_lines.append(f"- **买入日期**: {best_trade['entry_date']}")
        report_lines.append(f"- **卖出日期**: {best_trade['exit_date']}")
        report_lines.append(f"- **收益率**: {best_trade['profit']:.1%}")
        report_lines.append(f"- **持仓天数**: {best_trade['holding_days']}天")
        report_lines.append(f"- **离场原因**: {best_trade['reason']}")
        report_lines.append("")
        
        # 最差交易
        worst_trade = min(trade_history, key=lambda x: x['profit'])
        report_lines.append("### ❌ 最差交易")
        report_lines.append(f"- **股票**: {worst_trade['ts_code']}")
        report_lines.append(f"- **买入日期**: {worst_trade['entry_date']}")
        report_lines.append(f"- **卖出日期**: {worst_trade['exit_date']}")
        report_lines.append(f"- **收益率**: {worst_trade['profit']:.1%}")
        report_lines.append(f"- **持仓天数**: {worst_trade['holding_days']}天")
        report_lines.append(f"- **离场原因**: {worst_trade['reason']}")
        report_lines.append("")
    
    report_lines.append("## 💡 改进建议")
    report_lines.append("1. **优化入场时机**: 当前策略仅考虑阶段4/5，可尝试加入量能萎缩确认条件")
    report_lines.append("2. **动态仓位管理**: 根据市场环境调整仓位大小")
    report_lines.append("3. **过滤弱势行业**: 结合行业强度排名，避免买入弱势行业股票")
    report_lines.append("4. **参数敏感性分析**: 测试不同的底部评分阈值和持仓周期")
    report_lines.append("5. **加入止损优化**: 考虑使用ATR止损代替固定百分比")
    report_lines.append("")
    
    report_lines.append("---")
    report_lines.append("⚠️ 本报告仅供参考，不构成投资建议")
    report_lines.append(f"📊 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 保存报告
    if not os.path.exists(REPORT_DIR):
        os.makedirs(REPORT_DIR)
    
    filename = f"backtest_washout_2025H2.md"
    filepath = os.path.join(REPORT_DIR, filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write("\n".join(report_lines))
    
    print(f"报告已保存: {filepath}")
    return filepath, report_lines

def print_summary(metrics, trade_history):
    """打印回测摘要"""
    print("\n" + "="*60)
    print("📊 洗盘分析师策略回测摘要")
    print("="*60)
    print(f"总交易笔数: {metrics.get('total_trades', 0)}")
    print(f"胜率: {metrics.get('win_rate', 0):.1%}")
    print(f"平均盈利: {metrics.get('avg_win', 0):.1%}")
    print(f"平均亏损: {metrics.get('avg_loss', 0):.1%}")
    print(f"盈亏比: {metrics.get('profit_ratio', 0):.2f}")
    print(f"总收益率: {metrics.get('total_return', 0):.1%}")
    print(f"最大回撤: {metrics.get('max_drawdown', 0):.1%}")
    print(f"平均持仓天数: {metrics.get('avg_holding_days', 0):.1f}")
    print("="*60)
    
    if trade_history:
        best = max(trade_history, key=lambda x: x['profit'])
        worst = min(trade_history, key=lambda x: x['profit'])
        print(f"\n最佳交易: {best['ts_code']} 盈利 {best['profit']:.1%}")
        print(f"最差交易: {worst['ts_code']} 亏损 {worst['profit']:.1%}")

if __name__ == "__main__":
    # 运行回测
    trade_history, daily_equity, hs300_df = run_backtest()
    
    # 计算指标
    metrics = calculate_metrics(trade_history, daily_equity, 1000000)
    
    # 生成报告
    report_path, report_lines = generate_report(trade_history, daily_equity, hs300_df, metrics)
    
    # 打印摘要
    print_summary(metrics, trade_history)
    
    print(f"\n📄 完整报告已保存至: {report_path}")
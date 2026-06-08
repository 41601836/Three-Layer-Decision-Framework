# -*- coding: utf-8 -*-
"""
factor_analysis.py —— 因子深度挖掘与权重优化
================================================

功能：
1. 因子IC/IR分析：计算每个因子的IC值和分组收益
2. 新因子探索：北向资金连续流入、大宗交易溢价、缩量程度
3. 动态权重实验：进攻vs防守模式的权重组合测试

数据源：本地 SQLite (db/stock_daily.db)
回测区间：2024年上半年

用法：
    python factor_analysis.py
"""

import os
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

# 路径配置
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT_DIR, "db", "stock_daily.db")
REPORT_DIR = os.path.join(ROOT_DIR, "reports")

# 回测区间
BACKTEST_START = "20240101"
BACKTEST_END = "20240630"


def load_daily_data(conn: sqlite3.Connection, start_date: str, end_date: str) -> pd.DataFrame:
    """加载回测区间内的所有日线数据"""
    df = pd.read_sql("""
        SELECT ts_code, trade_date, open, high, low, close, vol, amount, pct_chg
        FROM daily_prices
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(start_date, end_date))
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df


def load_moneyflow_data(conn: sqlite3.Connection, start_date: str, end_date: str) -> pd.DataFrame:
    """加载资金流向数据"""
    df = pd.read_sql("""
        SELECT ts_code, trade_date, buy_elg_amount, sell_elg_amount,
               buy_lg_amount, sell_lg_amount, net_mf_amount
        FROM moneyflow
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(start_date, end_date))
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df


def load_holder_data(conn: sqlite3.Connection) -> pd.DataFrame:
    """加载股东户数数据"""
    df = pd.read_sql("""
        SELECT ts_code, end_date, holder_num
        FROM stk_holdernumber
        ORDER BY ts_code, end_date DESC
    """, conn)
    return df


def load_margin_data(conn: sqlite3.Connection, start_date: str, end_date: str) -> pd.DataFrame:
    """加载融资余额数据"""
    df = pd.read_sql("""
        SELECT ts_code, trade_date, rzye
        FROM stk_margin
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY ts_code, trade_date
    """, conn, params=(start_date, end_date))
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df


def load_hsgt_data(conn: sqlite3.Connection, start_date: str, end_date: str) -> pd.DataFrame:
    """加载北向资金数据"""
    df = pd.read_sql("""
        SELECT trade_date, north_money
        FROM hsgt_moneyflow
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY trade_date
    """, conn, params=(start_date, end_date))
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df


def load_block_trade_data(conn: sqlite3.Connection, start_date: str, end_date: str) -> pd.DataFrame:
    """加载大宗交易数据（如果有）"""
    try:
        df = pd.read_sql("""
            SELECT ts_code, trade_date, avg_premium
            FROM block_trade
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY ts_code, trade_date
        """, conn, params=(start_date, end_date))
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        return df
    except Exception:
        return pd.DataFrame()


def calculate_future_returns(df: pd.DataFrame, forward_days: int = 10) -> pd.DataFrame:
    """计算未来收益率"""
    df = df.sort_values(['ts_code', 'trade_date'])
    df['future_return'] = df.groupby('ts_code')['close'].shift(-forward_days)
    df['future_return'] = (df['future_return'] - df['close']) / df['close']
    return df.dropna(subset=['future_return'])


def calculate_factor_values(daily_df: pd.DataFrame, moneyflow_df: pd.DataFrame,
                           holder_df: pd.DataFrame, margin_df: pd.DataFrame) -> pd.DataFrame:
    """计算所有因子的数值"""
    print("[1/4] 计算因子数值...")

    # 合并数据
    df = daily_df.copy()

    # 因子1：主力资金净流入
    if not moneyflow_df.empty:
        moneyflow_df['net_main'] = (moneyflow_df['buy_elg_amount'] + moneyflow_df['buy_lg_amount']
                                     - moneyflow_df['sell_elg_amount'] - moneyflow_df['sell_lg_amount'])
        df = df.merge(moneyflow_df[['ts_code', 'trade_date', 'net_mf_amount', 'net_main']],
                     on=['ts_code', 'trade_date'], how='left')

    # 因子2：振幅（20日）
    df = df.sort_values(['ts_code', 'trade_date'])
    df['amplitude_20d'] = df.groupby('ts_code').apply(
        lambda x: (x['high'].rolling(20).max() - x['low'].rolling(20).min()) / x['low'].rolling(20).min()
    ).reset_index(level=0, drop=True)

    # 因子3：缩量程度（近20日均量/60日均量）
    df['vol_ma20'] = df.groupby('ts_code')['vol'].transform(lambda x: x.rolling(20).mean())
    df['vol_ma60'] = df.groupby('ts_code')['vol'].transform(lambda x: x.rolling(60).mean())
    df['volume_shrink'] = df['vol_ma20'] / df['vol_ma60']

    # 因子4：融资余额分位
    if not margin_df.empty:
        df = df.merge(margin_df[['ts_code', 'trade_date', 'rzye']],
                     on=['ts_code', 'trade_date'], how='left')

    # 因子5：三日背离（连续3日主力净流入但股价下跌）
    df['pct_chg_3d'] = df.groupby('ts_code')['pct_chg'].transform(lambda x: x.rolling(3).sum())
    df['net_main_3d'] = df.groupby('ts_code')['net_mf_amount'].transform(lambda x: x.rolling(3).sum())
    df['three_day_divergence'] = ((df['net_main_3d'] > 0) & (df['pct_chg_3d'] < 0)).astype(int)

    return df


def calculate_ic_ir(df: pd.DataFrame, factor_col: str, return_col: str = 'future_return') -> dict:
    """计算单个因子的IC和IR值"""
    # 去除极端值
    valid_df = df.dropna(subset=[factor_col, return_col])
    if len(valid_df) < 100:
        return {'ic': 0, 'ir': 0, 'count': len(valid_df)}

    # IC: 因子的横截面相关系数
    ic = valid_df[factor_col].corr(valid_df[return_col])

    # IR: IC的均值/标准差
    daily_ics = valid_df.groupby('trade_date').apply(
        lambda x: x[factor_col].corr(x[return_col]) if len(x) > 10 else np.nan
    ).dropna()

    ir = daily_ics.mean() / daily_ics.std() if daily_ics.std() > 0 else 0

    return {
        'ic': ic,
        'ir': ir,
        'ic_mean': daily_ics.mean(),
        'ic_std': daily_ics.std(),
        'count': len(valid_df)
    }


def calculate_group_returns(df: pd.DataFrame, factor_col: str, return_col: str = 'future_return',
                            n_groups: int = 5) -> pd.DataFrame:
    """计算因子分组收益"""
    valid_df = df.dropna(subset=[factor_col, return_col]).copy()

    if len(valid_df) < 100:
        return pd.DataFrame()

    # 按因子值分组
    valid_df['group'] = pd.qcut(valid_df[factor_col], n_groups, labels=False, duplicates='drop')

    # 计算每组的平均收益
    group_returns = valid_df.groupby('group')[return_col].agg(['mean', 'std', 'count'])
    group_returns.columns = ['mean_return', 'std_return', 'count']

    # 计算IC分组（前30% vs 后30%）
    high_ic = valid_df[valid_df['group'] >= n_groups - 1][return_col].mean()
    low_ic = valid_df[valid_df['group'] == 0][return_col].mean()
    group_returns['long_short'] = high_ic - low_ic

    return group_returns


def analyze_new_factors(df: pd.DataFrame, moneyflow_df: pd.DataFrame,
                        margin_df: pd.DataFrame, block_df: pd.DataFrame) -> dict:
    """分析新因子的有效性"""
    print("[2/4] 分析新因子...")

    new_factor_results = {}

    # 新因子1：北向资金连续流入（近5日净流入>0）
    if not moneyflow_df.empty:
        # 使用市场整体北向资金作为代理
        hsgt_df = pd.read_sql("""
            SELECT trade_date, north_money FROM hsgt_moneyflow
            WHERE trade_date >= ? ORDER BY trade_date
        """, sqlite3.connect(DB_PATH), params=(BACKTEST_START,))
        if not hsgt_df.empty:
            hsgt_df['trade_date'] = pd.to_datetime(hsgt_df['trade_date'])
            hsgt_df['north_inflow_5d'] = hsgt_df['north_money'].rolling(5).sum()
            hsgt_df['north_5d_positive'] = (hsgt_df['north_inflow_5d'] > 0).astype(int)

            # 合并到日线数据
            df = df.merge(hsgt_df[['trade_date', 'north_5d_positive']], on='trade_date', how='left')
            df['north_5d_positive'] = df['north_5d_positive'].fillna(0)

            # 计算IC
            ic_result = calculate_ic_ir(df, 'north_5d_positive')
            new_factor_results['北向资金连续流入'] = {
                'ic': ic_result['ic'],
                'ir': ic_result['ir'],
                'description': '近5日北向资金净流入>0',
                'effective': abs(ic_result['ic']) > 0.02
            }

    # 新因子2：缩量程度（近20日均量/60日均量<0.5）
    if 'volume_shrink' in df.columns:
        df['volume_shrink_factor'] = (df['volume_shrink'] < 0.5).astype(int)
        ic_result = calculate_ic_ir(df, 'volume_shrink_factor')
        new_factor_results['缩量程度'] = {
            'ic': ic_result['ic'],
            'ir': ic_result['ir'],
            'description': '近20日均量/60日均量<0.5',
            'effective': abs(ic_result['ic']) > 0.02
        }

    # 新因子3：融资余额下降
    if not margin_df.empty and 'rzye' in df.columns:
        df = df.sort_values(['ts_code', 'trade_date'])
        df['rzye_pct_change'] = df.groupby('ts_code')['rzye'].pct_change(20)
        df['margin_decline'] = (df['rzye_pct_change'] < -0.1).astype(int)
        ic_result = calculate_ic_ir(df, 'margin_decline')
        new_factor_results['融资余额下降'] = {
            'ic': ic_result['ic'],
            'ir': ic_result['ir'],
            'description': '近20日融资余额下降>10%',
            'effective': abs(ic_result['ic']) > 0.02
        }

    # 新因子4：三日背离（已在原因子中）
    if 'three_day_divergence' in df.columns:
        ic_result = calculate_ic_ir(df, 'three_day_divergence')
        new_factor_results['三日背离'] = {
            'ic': ic_result['ic'],
            'ir': ic_result['ir'],
            'description': '连续3日主力净流入但股价下跌',
            'effective': abs(ic_result['ic']) > 0.02
        }

    return new_factor_results, df


def analyze_existing_factors(df: pd.DataFrame) -> dict:
    """分析现有因子的IC/IR"""
    print("[3/4] 分析现有因子...")

    factors = {
        '主力资金净流入': 'net_mf_amount',
        '振幅偏低': 'amplitude_20d',
        '缩量程度': 'volume_shrink',
        '三日背离': 'three_day_divergence'
    }

    results = {}

    for name, col in factors.items():
        if col in df.columns:
            ic_result = calculate_ic_ir(df, col)
            results[name] = {
                'ic': round(ic_result['ic'], 4),
                'ir': round(ic_result['ir'], 4),
                'count': ic_result['count']
            }

    return results


def get_market_regime(conn: sqlite3.Connection, trade_date: str) -> str:
    """判断市场状态：进攻/防守"""
    try:
        # 获取近20日大盘涨跌
        df = pd.read_sql("""
            SELECT close FROM daily_index
            WHERE ts_code = '000001.SH' AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT 20
        """, conn, params=(trade_date,))

        if len(df) >= 20:
            pct_chg = (df['close'].iloc[0] - df['close'].iloc[-1]) / df['close'].iloc[-1]
            if pct_chg > 0.05:
                return 'offensive'  # 进攻
            elif pct_chg < -0.05:
                return 'defensive'  # 防守
            else:
                return 'neutral'
    except Exception:
        pass
    return 'neutral'


def dynamic_weight_experiment(df: pd.DataFrame, daily_df: pd.DataFrame) -> dict:
    """动态权重实验：进攻vs防守模式的权重组合"""
    print("[4/4] 测试动态权重...")

    conn = sqlite3.connect(DB_PATH)

    # 获取所有交易日
    dates = sorted(daily_df['trade_date'].unique())

    # 定义不同模式下的权重
    weight_configs = {
        'offensive': {
            '资金因子': 20,  # +5
            '筹码因子': 15,
            '技术因子': 10,
            '振幅因子': -10,
            '背离因子': 10
        },
        'defensive': {
            '资金因子': 10,
            '筹码因子': 20,  # +5
            '技术因子': 15,
            '振幅因子': -10,
            '背离因子': 5
        },
        'neutral': {
            '资金因子': 15,
            '筹码因子': 15,
            '技术因子': 10,
            '振幅因子': -10,
            '背离因子': 10
        }
    }

    # 回测两种策略
    results = {
        'dynamic': {'trades': [], 'wins': 0, 'total': 0},
        'static': {'trades': [], 'wins': 0, 'total': 0}
    }

    # 简化回测：计算每日因子得分并模拟交易
    for i in range(60, len(dates) - 10):
        date = dates[i]

        # 获取当日数据
        day_df = df[df['trade_date'] == date].dropna(subset=['net_mf_amount'])
        if day_df.empty:
            continue

        # 获取市场状态
        market_regime = get_market_regime(conn, date.strftime('%Y%m%d'))

        # 计算因子得分
        if 'net_mf_amount' in day_df.columns:
            day_df['money_score'] = (day_df['net_mf_amount'] > 0).astype(int) * 15
        if 'amplitude_20d' in day_df.columns:
            day_df['amp_score'] = (day_df['amplitude_20d'] < 0.3).astype(int) * -10
        if 'three_day_divergence' in day_df.columns:
            day_df['div_score'] = day_df['three_day_divergence'] * 10

        # 动态权重
        weights = weight_configs[market_regime]
        day_df['dynamic_score'] = (
            day_df.get('money_score', 0) * weights['资金因子'] / 15 +
            day_df.get('amp_score', 0) * weights['振幅因子'] / -10 +
            day_df.get('div_score', 0) * weights['背离因子'] / 10
        )

        # 静态权重
        day_df['static_score'] = (
            day_df.get('money_score', 0) +
            day_df.get('amp_score', 0) +
            day_df.get('div_score', 0)
        )

        # 选择高分股票
        if 'dynamic_score' in day_df.columns:
            top_dynamic = day_df.nlargest(5, 'dynamic_score')
            if len(top_dynamic) > 0 and 'future_return' in top_dynamic.columns:
                ret = top_dynamic['future_return'].mean()
                results['dynamic']['trades'].append(ret)
                if ret > 0:
                    results['dynamic']['wins'] += 1
                results['dynamic']['total'] += 1

        if 'static_score' in day_df.columns:
            top_static = day_df.nlargest(5, 'static_score')
            if len(top_static) > 0 and 'future_return' in top_static.columns:
                ret = top_static['future_return'].mean()
                results['static']['trades'].append(ret)
                if ret > 0:
                    results['static']['wins'] += 1
                results['static']['total'] += 1

    conn.close()

    # 计算最终指标
    for key in results:
        trades = results[key]['trades']
        if trades:
            results[key]['avg_return'] = np.mean(trades)
            results[key]['win_rate'] = results[key]['wins'] / results[key]['total'] if results[key]['total'] > 0 else 0

    return results, weight_configs


def generate_report(existing_factors: dict, new_factors: dict, dynamic_results: dict,
                    weight_configs: dict, output_path: str):
    """生成分析报告"""
    report = []
    report.append("# 📊 因子深度挖掘与权重优化分析报告")
    report.append("")
    report.append(f"**回测区间**: 2024年上半年 (2024-01-01 ~ 2024-06-30)")
    report.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append("")

    # 1. 现有因子IC/IR分析
    report.append("## 1️⃣ 现有因子IC/IR分析")
    report.append("")
    report.append("| 因子名称 | IC值 | IR值 | 样本数 | 评价 |")
    report.append("|----------|------|------|--------|------|")
    for name, data in existing_factors.items():
        ic = data['ic']
        if abs(ic) > 0.05:
            rating = "⭐⭐⭐ 强效因子"
        elif abs(ic) > 0.02:
            rating = "⭐⭐ 有效因子"
        elif abs(ic) > 0:
            rating = "⭐ 弱效因子"
        else:
            rating = "❌ 无效因子"
        report.append(f"| {name} | {ic:.4f} | {data['ir']:.4f} | {data['count']} | {rating} |")
    report.append("")

    # 2. 新因子探索结果
    report.append("## 2️⃣ 新因子探索结果")
    report.append("")
    report.append("| 新因子 | IC值 | IR值 | 描述 | 有效性 | 建议 |")
    report.append("|--------|------|------|------|--------|------|")
    for name, data in new_factors.items():
        effective = "✅ 有效" if data['effective'] else "⚠️ 待验证"
        suggestion = "建议加入评分" if data['effective'] else "建议扩大样本测试"
        report.append(f"| {name} | {data['ic']:.4f} | {data['ir']:.4f} | {data['description']} | {effective} | {suggestion} |")
    report.append("")

    # 3. 动态权重实验结果
    report.append("## 3️⃣ 动态权重实验结果")
    report.append("")
    report.append("### 权重配置")
    report.append("")
    report.append("| 模式 | 资金因子 | 筹码因子 | 技术因子 | 振幅因子 | 背离因子 |")
    report.append("|------|----------|----------|----------|----------|----------|")
    for mode, weights in weight_configs.items():
        mode_name = {"offensive": "进攻模式", "defensive": "防守模式", "neutral": "中性模式"}[mode]
        report.append(f"| {mode_name} | {weights['资金因子']} | {weights['筹码因子']} | {weights['技术因子']} | {weights['振幅因子']} | {weights['背离因子']} |")
    report.append("")

    report.append("### 回测表现")
    if 'dynamic' in dynamic_results:
        report.append("")
        report.append("| 策略类型 | 平均收益 | 交易次数 | 胜率 |")
        report.append("|----------|----------|----------|------|")
        if dynamic_results['dynamic']['total'] > 0:
            report.append(f"| 动态权重 | {dynamic_results['dynamic'].get('avg_return', 0):.2%} | {dynamic_results['dynamic']['total']} | {dynamic_results['dynamic'].get('win_rate', 0):.1%} |")
        if dynamic_results['static']['total'] > 0:
            report.append(f"| 静态权重 | {dynamic_results['static'].get('avg_return', 0):.2%} | {dynamic_results['static']['total']} | {dynamic_results['static'].get('win_rate', 0):.1%} |")
    report.append("")

    # 4. 优化建议
    report.append("## 4️⃣ 优化建议")
    report.append("")

    # 找出最有效的因子
    effective_factors = [(k, v) for k, v in existing_factors.items() if abs(v['ic']) > 0.02]
    effective_factors.sort(key=lambda x: abs(x[1]['ic']), reverse=True)

    report.append("### 因子权重优化建议")
    report.append("")
    report.append("基于IC/IR分析，建议以下权重调整：")
    report.append("")
    for i, (name, data) in enumerate(effective_factors[:3], 1):
        report.append(f"{i}. **{name}**: 当前权重建议 {'提高' if data['ic'] > 0 else '维持'}, IC={data['ic']:.4f}")
    report.append("")

    # 新因子建议
    valid_new = [k for k, v in new_factors.items() if v['effective']]
    if valid_new:
        report.append("### 新因子加入建议")
        report.append("")
        report.append(f"以下新因子验证有效，建议加入评分体系（+1~5分）：")
        for factor in valid_new:
            report.append(f"- {factor}: IC={new_factors[factor]['ic']:.4f}")
        report.append("")
        report.append("```python")
        report.append("# 示例：加入新因子")
        report.append("def add_new_factors(score):")
        for factor in valid_new:
            report.append(f"    if {factor.lower().replace(' ', '_')}_condition:")
            report.append("        score += 3  # 新增因子加分")
        report.append("    return score")
        report.append("```")
        report.append("")

    # 动态权重建议
    report.append("### 动态权重执行建议")
    report.append("")
    report.append("建议根据大盘环境切换权重配置：")
    report.append("- **进攻模式**（大盘涨幅>5%）: 资金因子权重+5")
    report.append("- **防守模式**（大盘跌幅>5%）: 筹码因子权重+5")
    report.append("- **中性模式**: 维持默认权重")
    report.append("")

    report.append("---")
    report.append("⚠️ 本报告仅供参考，不构成投资建议")
    report.append("📊 数据来源：本地数据库（2024年上半年）")

    # 写入文件
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report))

    print(f"\n报告已保存: {output_path}")
    return '\n'.join(report)


def main():
    """主函数"""
    print("=" * 60)
    print("📊 因子深度挖掘与权重优化分析")
    print("=" * 60)
    print(f"回测区间: {BACKTEST_START} ~ {BACKTEST_END}")
    print("")

    os.makedirs(REPORT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    try:
        # 加载数据
        print("[0/4] 加载数据...")
        daily_df = load_daily_data(conn, BACKTEST_START, BACKTEST_END)
        moneyflow_df = load_moneyflow_data(conn, BACKTEST_START, BACKTEST_END)
        holder_df = load_holder_data(conn)
        margin_df = load_margin_data(conn, BACKTEST_START, BACKTEST_END)
        block_df = load_block_trade_data(conn, BACKTEST_START, BACKTEST_END)

        print(f"  - 日线数据: {len(daily_df)} 条")
        print(f"  - 资金流向: {len(moneyflow_df)} 条")
        print(f"  - 股东户数: {len(holder_df)} 条")
        print(f"  - 融资余额: {len(margin_df)} 条")

        # 计算因子数值
        df = calculate_factor_values(daily_df, moneyflow_df, holder_df, margin_df)

        # 计算未来收益
        df = calculate_future_returns(df, forward_days=10)

        # 分析现有因子
        existing_factors = analyze_existing_factors(df)

        # 分析新因子
        new_factors, df = analyze_new_factors(df, moneyflow_df, margin_df, block_df)

        # 动态权重实验
        dynamic_results, weight_configs = dynamic_weight_experiment(df, daily_df)

        # 生成报告
        output_path = os.path.join(REPORT_DIR, "factor_analysis_report.md")
        report = generate_report(existing_factors, new_factors, dynamic_results,
                               weight_configs, output_path)

        print("\n" + "=" * 60)
        print("✅ 分析完成！")
        print("=" * 60)
        print(f"\n📄 完整报告: {output_path}")
        print("\n" + report)

    finally:
        conn.close()


if __name__ == "__main__":
    main()

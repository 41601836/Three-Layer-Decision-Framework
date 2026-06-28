# -*- coding: utf-8 -*-
"""
展示参数扫描结果，格式化为对比表格
"""
import pandas as pd
import os

RESULT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "reports", "param_scan_results.tsv"
)

if not os.path.exists(RESULT_FILE):
    print("结果文件尚未生成，请等待扫描完成")
    exit(1)

df = pd.read_csv(RESULT_FILE, sep='\t', dtype=str)
df.columns = df.columns.str.strip()

# 数值转换
num_cols = ['top_pct','hold_days','trades','win_rate','avg_ret','total_ret','annual_ret','max_dd','sharpe']
for c in num_cols:
    if c in df.columns:
        df[c] = pd.to_numeric(df[c], errors='coerce')

# 分组展示
print("=" * 75)
print(" 参数扫描结果汇总")
print("=" * 75)

# 样本内
in_df = df[df['type'] == 'in_sample'].copy()
if not in_df.empty:
    # top_pct 扫描（hold=10）
    pct_scan = in_df[in_df['hold_days'] == 10].sort_values('top_pct')
    if not pct_scan.empty:
        print("\n【top_pct 扫描】hold_days=10 天，2025全年")
        print(f"{'top_pct':>8} {'笔数':>6} {'胜率':>7} {'单笔均收':>9} {'年化收益':>9} {'最大回撤':>9} {'夏普':>7}")
        print("─" * 65)
        best_sharpe = pct_scan['sharpe'].max()
        for _, r in pct_scan.iterrows():
            marker = " ★" if r['sharpe'] == best_sharpe else ""
            print(f"  {int(r['top_pct']):>4}%  {int(r['trades']):>6}  {r['win_rate']:>5.1f}%  "
                  f"  {r['avg_ret']:>+6.2f}%  {r['annual_ret']:>+7.2f}%  {r['max_dd']:>+7.2f}%  {r['sharpe']:>6.3f}{marker}")

    # hold_days 扫描（pct=15）
    day_scan = in_df[in_df['top_pct'] == 15].sort_values('hold_days')
    if len(day_scan) > 1:
        print(f"\n【hold_days 扫描】top_pct=15%，2025全年")
        print(f"{'hold_days':>10} {'笔数':>6} {'胜率':>7} {'单笔均收':>9} {'年化收益':>9} {'最大回撤':>9} {'夏普':>7}")
        print("─" * 65)
        best_sharpe = day_scan['sharpe'].max()
        for _, r in day_scan.iterrows():
            marker = " ★" if r['sharpe'] == best_sharpe else ""
            print(f"  {int(r['hold_days']):>6}天  {int(r['trades']):>6}  {r['win_rate']:>5.1f}%  "
                  f"  {r['avg_ret']:>+6.2f}%  {r['annual_ret']:>+7.2f}%  {r['max_dd']:>+7.2f}%  {r['sharpe']:>6.3f}{marker}")

# 样本外
oos_df = df[df['type'] == 'out_of_sample'].copy()
if not oos_df.empty:
    print(f"\n【样本外验证】2026年1月1日 ~ 6月25日")
    print(f"{'top_pct':>8} {'hold':>6} {'笔数':>6} {'胜率':>7} {'年化收益':>9} {'最大回撤':>9} {'夏普':>7}")
    print("─" * 65)
    for _, r in oos_df.iterrows():
        pct_val = r.get('top_pct', '?')
        days_val = r.get('hold_days', '?')
        trades_val = r.get('trades', '?')
        print(f"  {pct_val:>4}%  {days_val:>4}天  "
              f"{int(trades_val) if pd.notna(trades_val) else '?':>6}  "
              f"{r['win_rate']:>5.1f}%  {r['annual_ret']:>+7.2f}%  "
              f"{r['max_dd']:>+7.2f}%  {r['sharpe']:>6.3f}")

print("\n★ = 最优夏普比率")
print("=" * 75)

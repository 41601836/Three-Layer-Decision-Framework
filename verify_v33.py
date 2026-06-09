import sys
sys.path.insert(0, 'd:/StockAI')
from analyze_stock import StockAnalyzer

a = StockAnalyzer()
sc, reasoning = a.analyze_v3_0('000001.SZ')
print('=== 000001.SZ analyze_v3_0 验证 (v3.3) ===')
print(f'总分: {sc["total_score"]}')
print('reasoning:')
for r in reasoning:
    print(f'  {r}')
print()

has_positive_amp = any(k in r for r in reasoning
                       for k in ['振幅强收敛', '振幅收敛', '+8分', '+5分振幅', '+2分振幅'])
has_amp_veto     = any('振幅风险警示' in r or '振幅异常' in r or '波动正常' in r for r in reasoning)
has_margin_low   = any('融资低位' in r and '+' in r for r in reasoning)
has_divergence   = any('增强信号' in r for r in reasoning)

print(f'振幅正向加分（应为False）: {has_positive_amp}')
print(f'振幅风险过滤逻辑存在（应为True）: {has_amp_veto}')
print(f'融资低位加分（应为False）: {has_margin_low}')
print(f'三日背离标注[增强信号]（有则显示）: {has_divergence}')

ok = not has_positive_amp and not has_margin_low and has_amp_veto
print()
print('✅ 任务1&2 验证通过！' if ok else '❌ 任务1&2 验证失败，请检查！')
a.close()

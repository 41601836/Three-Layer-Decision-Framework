# -*- coding: utf-8 -*-
"""
策略总结报告 - 梳理当前所有策略
"""
import os
import json
import numpy as np

def load_strategy_results():
    """加载所有策略训练结果"""
    results = {}
    hunter_dir = os.path.join(os.path.dirname(__file__), 'win_rate_hunter')
    
    # 主策略训练结果
    main_result = os.path.join(hunter_dir, 'training_result_70pct.json')
    if os.path.exists(main_result):
        with open(main_result, 'r', encoding='utf-8') as f:
            results['main'] = json.load(f)
    
    # 分阶段训练结果
    for stage in [1, 2, 3]:
        stage_result = os.path.join(hunter_dir, f'training_result_stage{stage}.json')
        if os.path.exists(stage_result):
            with open(stage_result, 'r', encoding='utf-8') as f:
                results[f'stage{stage}'] = json.load(f)
    
    # 融合策略
    fused_result = os.path.join(hunter_dir, 'fused_strategy_v2.json')
    if os.path.exists(fused_result):
        with open(fused_result, 'r', encoding='utf-8') as f:
            results['fused'] = json.load(f)
    
    return results

def format_number(value, decimals=2):
    """格式化数字"""
    if isinstance(value, (int, float, np.integer, np.floating)):
        return f"{value:.{decimals}%}" if value < 10 else f"{value:.{decimals}f}"
    return str(value)

def print_strategy_summary(results):
    """打印策略总结"""
    print("="*80)
    print("                    📊 策略库总结报告")
    print("="*80)
    
    # 1. 主策略（v1.0版本）
    print("\n" + "="*60)
    print(" 1️⃣ 主策略 (v1.0 - 75%胜率)")
    print("="*60)
    if 'main' in results:
        strategy = results['main']['best_strategy']
        result = results['main']['backtest_result']
        
        print(f"   策略名称: {strategy['name']}")
        print(f"   训练周期: 20260101 ~ 20260625")
        print(f"   因子组合:")
        for i, factor_name in enumerate(strategy['factors']):
            weight = strategy['weights'][i]
            threshold = strategy['thresholds'].get(factor_name, 'N/A')
            print(f"     - {factor_name} (权重: {weight:.2f}, 阈值: {threshold})")
        print(f"   持有天数: {strategy['hold_days']}")
        print(f"\n   回测绩效:")
        print(f"     胜率: {format_number(result['win_rate'], 1)}")
        print(f"     收益: {format_number(result['total_return'], 2)}")
        print(f"     回撤: {format_number(result['max_drawdown'], 2)}")
        print(f"     信号数: {result['signal_count']}")
        print(f"     交易数: {result['trade_count']}")
    else:
        print("   ❌ 未找到主策略")
    
    # 2. 分阶段策略
    print("\n" + "="*60)
    print(" 2️⃣ 分阶段策略")
    print("="*60)
    
    stages = [
        ('stage1', '第一阶段', '1-2月', '弱势市场'),
        ('stage2', '第二阶段', '3-4月', '中性市场'),
        ('stage3', '第三阶段', '5-6月', '强势市场'),
    ]
    
    for key, name, period, market in stages:
        if key in results:
            strategy = results[key]['best_strategy']
            result = results[key]['backtest_result']
            
            print(f"\n   {name} ({period} - {market})")
            print(f"   ├─ 策略: {strategy['name']}")
            print(f"   ├─ 因子数: {len(strategy['factors'])}")
            print(f"   ├─ 持有天数: {strategy['hold_days']}")
            print(f"   ├─ 胜率: {format_number(result['win_rate'], 1)}")
            print(f"   ├─ 收益: {format_number(result['total_return'], 2)}")
            print(f"   ├─ 回撤: {format_number(result['max_drawdown'], 2)}")
            print(f"   └─ 信号数: {result['signal_count']}")
    
    # 3. 融合策略
    print("\n" + "="*60)
    print(" 3️⃣ 融合策略 (第三阶段空仓)")
    print("="*60)
    if 'fused' in results:
        result = results['fused']['backtest_result']
        
        print(f"   策略类型: {results['fused']['strategy_type']}")
        print(f"   第三阶段: {'空仓' if results['fused']['skip_stage3'] else '执行策略'}")
        print(f"\n   综合绩效 (1-4月):")
        print(f"     胜率: {format_number(result['win_rate'], 1)}")
        print(f"     收益: {format_number(result['total_return'], 2)}")
        print(f"     回撤: {format_number(result['max_drawdown'], 2)}")
        print(f"     信号数: {result['signal_count']}")
        print(f"     交易数: {result['trade_count']}")
    else:
        print("   ❌ 未找到融合策略")
    
    # 4. 策略对比
    print("\n" + "="*60)
    print(" 4️⃣ 策略对比")
    print("="*60)
    
    print(f"\n   {'策略':<10} {'胜率':<8} {'收益':<12} {'回撤':<10} {'信号数':<6}")
    print("   " + "-"*50)
    
    if 'main' in results:
        r = results['main']['backtest_result']
        print(f"   {'主策略':<10} {format_number(r['win_rate'],1):<8} {format_number(r['total_return'],2):<12} {format_number(r['max_drawdown'],2):<10} {r['signal_count']:<6}")
    
    for key, name, _, _ in stages:
        if key in results:
            r = results[key]['backtest_result']
            print(f"   {name:<10} {format_number(r['win_rate'],1):<8} {format_number(r['total_return'],2):<12} {format_number(r['max_drawdown'],2):<10} {r['signal_count']:<6}")
    
    if 'fused' in results:
        r = results['fused']['backtest_result']
        print(f"   {'融合策略':<10} {format_number(r['win_rate'],1):<8} {format_number(r['total_return'],2):<12} {format_number(r['max_drawdown'],2):<10} {r['signal_count']:<6}")
    
    # 5. 版本固化信息
    print("\n" + "="*60)
    print(" 5️⃣ 版本固化")
    print("="*60)
    
    release_dir = os.path.join(os.path.dirname(__file__), 'release', 'v1.0_75pct')
    if os.path.exists(release_dir):
        files = os.listdir(release_dir)
        print(f"   版本目录: release/v1.0_75pct/")
        print(f"   文件数: {len(files)}")
        print(f"   包含: {', '.join(files[:5])}{'...' if len(files) > 5 else ''}")
    else:
        print("   ❌ 版本目录不存在")

def main():
    results = load_strategy_results()
    print_strategy_summary(results)
    
    print("\n" + "="*80)
    print("                      📋 策略使用建议")
    print("="*80)
    print("""
   🔹 主策略 (75%胜率): 适用于完整周期，表现稳定
   🔹 阶段1策略 (67%胜率): 适用于弱势市场（1-2月）
   🔹 阶段2策略 (100%胜率): 信号极少，需谨慎使用
   🔹 阶段3策略 (53%胜率): 强势市场表现不佳，建议空仓
   🔹 融合策略 (68%胜率): 综合方案，第三阶段空仓
   
   💡 推荐使用: 融合策略或主策略
""")

if __name__ == "__main__":
    main()
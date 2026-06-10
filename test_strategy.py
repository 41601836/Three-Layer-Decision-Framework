#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试策略加载和信号生成"""
import os
import sys
import json

# 添加项目根目录到路径
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

from strategies.manager import StrategyManager


def test_strategy():
    print("=" * 60)
    print("📋 测试策略加载和信号生成")
    print("=" * 60)
    
    # 加载配置
    config_path = os.path.join(ROOT_DIR, 'config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 数据库路径
    db_path = os.path.join(ROOT_DIR, 'db', 'stock_daily.db')
    
    try:
        # 初始化策略管理器
        manager = StrategyManager(db_path, config)
        
        print(f"\n📊 可用策略: {manager.list_available_strategies()}")
        print(f"🚀 启用策略: {manager.list_enabled_strategies()}")
        
        # 测试信号生成
        trade_date = '20260609'
        print(f"\n🔍 开始生成 {trade_date} 的交易信号...")
        
        all_signals = manager.get_all_signals(trade_date)
        
        for strategy_name, signals in all_signals.items():
            print(f"\n📈 {strategy_name} 生成了 {len(signals)} 个信号")
            
            if signals:
                # 显示第一个信号详情
                first_signal = signals[0]
                print("\n📋 第一个信号详情:")
                print(f"   股票代码: {first_signal['ts_code']}")
                print(f"   股票名称: {first_signal['stock_name']}")
                print(f"   信号类型: {first_signal['signal_type']}")
                print(f"   综合评分: {first_signal['score']}")
                print(f"   收盘价: {first_signal['close_price']:.2f}")
                print(f"   涨跌幅: {first_signal['pct_chg']:.2f}%")
                print(f"   主力资金: {first_signal['main_money']/10000:.1f}万")
                print(f"   股东变化: {first_signal['holder_chg']:.2%}")
                
                # 测试交易计划生成
                strategy = manager.get_strategy_instance(strategy_name)
                if strategy:
                    trade_plan = strategy.get_trade_plan(first_signal['ts_code'], first_signal)
                    print("\n💼 交易计划:")
                    print(f"   理想买入区间: {trade_plan.get('buy_range', {}).get('ideal_low', 0):.2f} - {trade_plan.get('buy_range', {}).get('ideal_high', 0):.2f}")
                    print(f"   建议仓位: {trade_plan.get('position_pct', 0)*100:.1f}%")
                    print(f"   初始止损价: {trade_plan.get('stop_loss_initial', 0):.2f}")
                
                # 测试推送卡片生成
                push_card = strategy.get_push_card(first_signal)
                print("\n📱 推送卡片HTML预览:")
                print(push_card[:500] + "..." if len(push_card) > 500 else push_card)
        
        print("\n✅ 策略测试完成!")
        
    except Exception as e:
        print(f"\n❌ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_strategy()

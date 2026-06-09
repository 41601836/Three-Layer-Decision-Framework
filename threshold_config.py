# -*- coding: utf-8 -*-
"""
阈值配置文件 (基于2024上半年回测验证结果优化)
================================================

回测验证数据（64.8万条信号）：
- 得分≥30：10日胜率60.7%，10日均收益+4.12%，盈亏比1.48
- 得分≥15：10日胜率43.3%，10日均收益-0.57%，盈亏比1.15
- 得分≥0：10日胜率37.9%，10日均收益-1.92%，盈亏比1.02

优化策略：
1. 强信号阈值保持≥30（胜率优异，表现稳定）
2. 中信号阈值保持≥15（可作为观察池）
3. 低于15分过滤（表现较差，避免噪音）
4. 各模式仓位上限根据得分动态调整
"""

# === 信号强度阈值 ===
SIGNAL_THRESHOLD = {
    'strong': 30,      # 强信号阈值
    'medium': 15,      # 中信号阈值
    'filter': 0,       # 过滤阈值（低于此值不输出）
}

# === 各模式仓位上限（按得分等级）===
POSITION_LIMITS = {
    'attack': {        # 进攻模式
        'strong': 0.15,  # 强信号最大仓位 15%
        'medium': 0.08,  # 中信号最大仓位 8%
    },
    'defense': {       # 防守模式
        'strong': 0.10,  # 强信号最大仓位 10%
        'medium': 0.05,  # 中信号最大仓位 5%
    },
    'neutral': {       # 中性模式
        'strong': 0.05,  # 强信号最大仓位 5%
        'medium': 0.02,  # 中信号最大仓位 2%
    },
}

# === AI分析门槛 ===
AI_ANALYSIS_THRESHOLD = 40  # 高于此分才进行AI深度分析

# === 单只股票最大仓位 ===
MAX_SINGLE_STOCK_POSITION = 0.08  # 单只股票最大仓位 8%

# === 策略参数 ===
STRATEGY_PARAMS = {
    'name': '主力资金提前嗅探·横盘吸筹识别器',
    'version': 'v3.2',
    'validation_date': '2026-06-05',
    'win_rate_10d': 60.7,      # 强信号10日胜率
    'avg_return_10d': 4.12,    # 强信号10日均收益
    'profit_loss_ratio': 1.48,  # 强信号盈亏比
    'sample_count': 42914,      # 强信号样本数
}

def get_position_limit(score: float, market_mode: str = 'defense') -> float:
    """
    根据得分和市场模式获取仓位上限。
    
    参数：
        score: 综合得分
        market_mode: 市场模式 (attack/defense/neutral)
    
    返回：
        仓位上限（0-1之间）
    """
    limits = POSITION_LIMITS.get(market_mode, POSITION_LIMITS['defense'])
    
    if score >= SIGNAL_THRESHOLD['strong']:
        return limits['strong']
    elif score >= SIGNAL_THRESHOLD['medium']:
        return limits['medium']
    else:
        return 0.0

def get_signal_strength(score: float) -> str:
    """
    根据得分判断信号强度等级。
    
    返回：'strong' | 'medium' | 'weak' | 'filtered'
    """
    if score >= SIGNAL_THRESHOLD['strong']:
        return 'strong'
    elif score >= SIGNAL_THRESHOLD['medium']:
        return 'medium'
    elif score >= SIGNAL_THRESHOLD['filter']:
        return 'weak'
    else:
        return 'filtered'

if __name__ == '__main__':
    print("阈值配置信息:")
    print(f"策略名称: {STRATEGY_PARAMS['name']}")
    print(f"版本: {STRATEGY_PARAMS['version']}")
    print(f"验证日期: {STRATEGY_PARAMS['validation_date']}")
    print(f"\n强信号阈值: ≥{SIGNAL_THRESHOLD['strong']}")
    print(f"中信号阈值: ≥{SIGNAL_THRESHOLD['medium']}")
    print(f"过滤阈值: <{SIGNAL_THRESHOLD['filter']}")
    print(f"\n回测表现（强信号）:")
    print(f"  10日胜率: {STRATEGY_PARAMS['win_rate_10d']}%")
    print(f"  10日均收益: {STRATEGY_PARAMS['avg_return_10d']}%")
    print(f"  盈亏比: {STRATEGY_PARAMS['profit_loss_ratio']}")
    print(f"  样本数: {STRATEGY_PARAMS['sample_count']:,}")
# -*- coding: utf-8 -*-
"""
分阶段训练配置文件
根据不同市场环境设置不同的训练参数
"""

STAGE_CONFIG = {
    'stage_1': {
        'name': '第一阶段（弱势市场）',
        'period': '20260101_20260228',
        'start_date': '20260101',
        'end_date': '20260228',
        'threshold_loose': 0.1,
        'target_win_rate': 0.60,
        'min_signal_count': 10,
        'population_size': 20,
        'generations': 20
    },
    'stage_2': {
        'name': '第二阶段（中性市场）',
        'period': '20260301_20260430',
        'start_date': '20260301',
        'end_date': '20260430',
        'threshold_loose': 0.15,
        'target_win_rate': 0.70,
        'min_signal_count': 15,
        'population_size': 25,
        'generations': 25
    },
    'stage_3': {
        'name': '第三阶段（强势市场）',
        'period': '20260501_20260625',
        'start_date': '20260501',
        'end_date': '20260625',
        'threshold_loose': 0.20,
        'target_win_rate': 0.75,
        'min_signal_count': 20,
        'population_size': 30,
        'generations': 30
    }
}

def get_stage_by_date(date):
    """根据日期判断所属阶段"""
    if '202601' <= date <= '20260228':
        return 'stage_1'
    elif '202603' <= date <= '20260430':
        return 'stage_2'
    elif '202605' <= date <= '20260625':
        return 'stage_3'
    return 'stage_2'
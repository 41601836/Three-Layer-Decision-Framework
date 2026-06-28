# -*- coding: utf-8 -*-
"""
胜率猎手 Agent - 基于遗传算法的量化策略自动进化系统
"""

__version__ = "1.0.0"
__author__ = "WinRateHunter Team"
__description__ = "基于遗传算法的量化策略自动进化系统，目标胜率≥70%"

from .factor_pool import FactorPool, Factor, PriceFactor, VolumeFactor, MoneyFactor, ChipsFactor, FundamentalFactor
from .strategy import Strategy, StrategyGenerator
from .backtest_executor import BacktestExecutor
from .evolution_engine import EvolutionEngine

__all__ = [
    'FactorPool', 'Factor', 'PriceFactor', 'VolumeFactor', 'MoneyFactor', 'ChipsFactor', 'FundamentalFactor',
    'Strategy', 'StrategyGenerator',
    'BacktestExecutor',
    'EvolutionEngine'
]
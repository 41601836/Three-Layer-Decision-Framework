from abc import ABC, abstractmethod
from typing import List, Dict, Any
from enum import Enum
import sqlite3
import json


class SignalType(Enum):
    """信号类型枚举"""
    BUY = 'buy'
    SELL = 'sell'
    HOLD = 'hold'
    WARNING = 'warning'


class BaseStrategy(ABC):
    """策略基类，所有策略必须继承此类并实现抽象方法"""
    
    def __init__(self, db_path: str, config: Dict[str, Any]):
        """
        初始化策略
        
        Args:
            db_path: 数据库文件路径
            config: 策略配置字典
        """
        self.db_path = db_path
        self.config = config
        self.strategy_name = self.__class__.__name__
        
    def get_db_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        return sqlite3.connect(self.db_path)
    
    @abstractmethod
    def get_signals(self, trade_date: str) -> List[Dict[str, Any]]:
        """
        生成指定交易日的交易信号
        
        Args:
            trade_date: 交易日，格式如 '20260610'
            
        Returns:
            信号列表，每个信号为字典，包含至少 'ts_code' 和 'signal_type' 字段
        """
        pass
    
    @abstractmethod
    def get_trade_plan(self, ts_code: str, signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        根据股票代码和信号生成交易计划
        
        Args:
            ts_code: 股票代码
            signal: 交易信号字典
            
        Returns:
            交易计划字典，包含买卖方向、价格、仓位等信息
        """
        pass
    
    @abstractmethod
    def get_push_card(self, signal: Dict[str, Any]) -> str:
        """
        生成用于推送的卡片内容（HTML格式）
        
        Args:
            signal: 交易信号字典
            
        Returns:
            HTML格式的推送卡片内容
        """
        pass

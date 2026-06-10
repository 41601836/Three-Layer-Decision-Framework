import os
import importlib
import inspect
from typing import Dict, List, Type, Any
from .base_strategy import BaseStrategy


class StrategyManager:
    """策略管理器，负责加载、管理和执行所有策略"""
    
    def __init__(self, db_path: str, config: Dict[str, Any]):
        """
        初始化策略管理器
        
        Args:
            db_path: 数据库文件路径
            config: 全局配置字典
        """
        self.db_path = db_path
        self.config = config
        self.strategy_classes: Dict[str, Type[BaseStrategy]] = {}
        self.strategy_instances: Dict[str, BaseStrategy] = {}
        
        # 扫描并注册所有策略
        self._scan_strategies()
        
        # 加载启用的策略
        self._load_enabled_strategies()
    
    def _scan_strategies(self):
        """扫描所有策略文件并注册策略类"""
        # 扫描内置策略目录
        self._scan_directory(os.path.dirname(__file__))
        
        # 扫描自定义策略目录
        custom_dir = os.path.join(os.path.dirname(__file__), 'custom')
        self._scan_directory(custom_dir)
    
    def _scan_directory(self, directory: str):
        """扫描指定目录下的策略文件"""
        if not os.path.exists(directory):
            return
            
        for filename in os.listdir(directory):
            if filename.endswith('.py') and filename != '__init__.py':
                module_name = filename[:-3]
                module_path = os.path.join(directory, filename)
                
                # 计算相对导入路径
                relative_path = os.path.relpath(module_path, os.path.dirname(__file__))
                if relative_path.startswith('custom'):
                    import_path = f'.custom.{module_name}'
                else:
                    import_path = f'.{module_name}'
                
                try:
                    # 导入模块
                    module = importlib.import_module(import_path, package='strategies')
                    
                    # 查找BaseStrategy的子类
                    for name, obj in inspect.getmembers(module, inspect.isclass):
                        if obj != BaseStrategy and issubclass(obj, BaseStrategy):
                            self.strategy_classes[obj.__name__] = obj
                            print(f"已注册策略: {obj.__name__}")
                            
                except Exception as e:
                    print(f"加载策略模块 {module_name} 失败: {e}")
    
    def _load_enabled_strategies(self):
        """加载配置中启用的策略"""
        enabled_strategies = self.config.get('enabled_strategies', [])
        
        for strategy_name in enabled_strategies:
            if strategy_name in self.strategy_classes:
                try:
                    strategy_class = self.strategy_classes[strategy_name]
                    strategy_instance = strategy_class(self.db_path, self.config)
                    self.strategy_instances[strategy_name] = strategy_instance
                    print(f"已加载策略: {strategy_name}")
                except Exception as e:
                    print(f"初始化策略 {strategy_name} 失败: {e}")
            else:
                print(f"未找到策略: {strategy_name}")
    
    def get_all_signals(self, trade_date: str) -> Dict[str, List[Dict[str, Any]]]:
        """
        获取所有启用策略的交易信号
        
        Args:
            trade_date: 交易日，格式如 '20260610'
            
        Returns:
            所有策略的信号字典，键为策略名，值为信号列表
        """
        all_signals = {}
        
        for strategy_name, strategy in self.strategy_instances.items():
            try:
                signals = strategy.get_signals(trade_date)
                all_signals[strategy_name] = signals
                print(f"策略 {strategy_name} 生成 {len(signals)} 个信号")
            except Exception as e:
                print(f"策略 {strategy_name} 生成信号失败: {e}")
                all_signals[strategy_name] = []
        
        return all_signals
    
    def get_strategy_instance(self, strategy_name: str) -> BaseStrategy:
        """
        获取指定策略的实例
        
        Args:
            strategy_name: 策略类名
            
        Returns:
            策略实例，如果未找到则返回None
        """
        return self.strategy_instances.get(strategy_name)
    
    def reload_strategies(self):
        """重新加载所有策略"""
        self.strategy_classes.clear()
        self.strategy_instances.clear()
        
        self._scan_strategies()
        self._load_enabled_strategies()
        
    def list_available_strategies(self) -> List[str]:
        """获取所有可用策略列表"""
        return list(self.strategy_classes.keys())
    
    def list_enabled_strategies(self) -> List[str]:
        """获取所有启用策略列表"""
        return list(self.strategy_instances.keys())
    
    def get_strategy_status(self) -> Dict[str, str]:
        """
        获取所有策略的状态
        
        Returns:
            策略状态字典，键为策略名，值为状态（ready/running/error）
        """
        status = {}
        for strategy_name, strategy in self.strategy_instances.items():
            status[strategy_name] = 'ready'
        return status
    
    def scan_strategies(self):
        """扫描策略（公共接口）"""
        self._scan_strategies()
    
    def load_enabled_strategies(self):
        """加载启用的策略（公共接口）"""
        self._load_enabled_strategies()
    
    def get_market_signals(self, trade_date: str = None) -> List[Dict[str, Any]]:
        """
        获取市场信号
        
        Args:
            trade_date: 交易日，可选
            
        Returns:
            市场信号列表
        """
        all_signals = []
        for strategy_name, strategy in self.strategy_instances.items():
            try:
                signals = strategy.get_signals(trade_date)
                all_signals.extend(signals)
            except Exception as e:
                print(f"获取策略 {strategy_name} 市场信号失败: {e}")
        return all_signals


# 全局策略管理器实例
_strategy_manager_instance = None


def get_strategy_manager(config: Dict[str, Any] = None) -> StrategyManager:
    """
    获取策略管理器单例实例
    
    Args:
        config: 配置字典，如果为None则使用默认配置
        
    Returns:
        策略管理器实例
    """
    global _strategy_manager_instance
    
    if _strategy_manager_instance is None:
        if config is None:
            # 加载默认配置
            import json
            config_path = os.path.join(os.path.dirname(__file__), '..', 'config.json')
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        
        db_path = config.get('database', {}).get('path', 'stock_data.db')
        _strategy_manager_instance = StrategyManager(db_path, config)
    
    return _strategy_manager_instance

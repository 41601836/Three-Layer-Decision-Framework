#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
性能监控和告警模块
监控数据拉取性能，检测异常并触发告警
"""
import os
import json
import time
import logging
from datetime import datetime, timedelta, time as dt_time
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, asdict
from enum import Enum

log = logging.getLogger(__name__)


class AlertLevel(Enum):
    """告警级别"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class PerformanceMetric:
    """性能指标"""
    timestamp: str
    operation: str
    duration_seconds: float
    records_processed: int
    success: bool
    error_message: str = ""
    metadata: Dict = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
    
    @property
    def records_per_second(self) -> float:
        """每秒处理记录数"""
        if self.duration_seconds > 0:
            return self.records_processed / self.duration_seconds
        return 0.0


@dataclass
class Alert:
    """告警信息"""
    timestamp: str
    level: AlertLevel
    message: str
    metric: PerformanceMetric
    threshold: float
    actual_value: float


class PerformanceMonitor:
    """性能监控器"""
    
    def __init__(self, 
                 alert_thresholds: Dict[str, float] = None,
                 alert_callback: Callable = None,
                 enable_feishu_alert: bool = False):
        """
        初始化性能监控器
        
        Args:
            alert_thresholds: 告警阈值配置
            alert_callback: 告警回调函数
            enable_feishu_alert: 是否启用飞书告警
        """
        self.metrics: List[PerformanceMetric] = []
        self.alerts: List[Alert] = []
        self.enable_feishu_alert = enable_feishu_alert
        
        # 默认告警阈值
        self.alert_thresholds = alert_thresholds or {
            'max_duration_per_day': 6.0,      # 每天最大耗时（秒）
            'min_records_per_second': 1000,   # 最小每秒记录数
            'max_error_rate': 0.05,            # 最大错误率（5%）
            'max_duration_total': 30.0,       # 总任务最大耗时（秒）
            'min_new_rows': 1,                # 最小新增行数（检测接口故障）
        }
        
        self.alert_callback = alert_callback or self._default_alert_callback
        
    def record_metric(self, metric: PerformanceMetric):
        """记录性能指标"""
        self.metrics.append(metric)
        self._check_alerts(metric)
        log.debug(f"记录指标: {metric.operation} - {metric.duration_seconds:.2f}s, "
                  f"{metric.records_processed} 条记录, 成功: {metric.success}")
    
    def _check_alerts(self, metric: PerformanceMetric):
        """检查是否需要告警"""
        alerts = []
        
        # 检查单日耗时
        if 'daily' in metric.operation.lower() or 'date' in metric.operation.lower():
            duration_per_day = metric.duration_seconds
            if duration_per_day > self.alert_thresholds['max_duration_per_day']:
                alert = Alert(
                    timestamp=datetime.now().isoformat(),
                    level=AlertLevel.WARNING,
                    message=f"单日拉取耗时过长: {duration_per_day:.2f}s > {self.alert_thresholds['max_duration_per_day']}s",
                    metric=metric,
                    threshold=self.alert_thresholds['max_duration_per_day'],
                    actual_value=duration_per_day
                )
                alerts.append(alert)
        
        # 检查处理速度
        if metric.records_per_second < self.alert_thresholds['min_records_per_second']:
            # 检查是否是正常的"无数据"情况（如当天数据还未生成）
            is_normal_slow = False
            
            if 'date' in metric.metadata:
                target_date = metric.metadata['date']
                today = datetime.now()
                today_str = today.strftime("%Y%m%d")
                
                # 如果尝试拉取的是当天数据，且时间在15:30前，处理速度慢是正常的
                if target_date == today_str and today.time() < dt_time(15, 30):
                    is_normal_slow = True
            
            if not is_normal_slow:
                alert = Alert(
                    timestamp=datetime.now().isoformat(),
                    level=AlertLevel.WARNING,
                    message=f"处理速度过慢: {metric.records_per_second:.0f} 条/秒 < {self.alert_thresholds['min_records_per_second']} 条/秒",
                    metric=metric,
                    threshold=self.alert_thresholds['min_records_per_second'],
                    actual_value=metric.records_per_second
                )
                alerts.append(alert)
        
        # 检查新增行数为0（可能接口故障）
        if metric.records_processed == 0 and metric.success:
            # 检查是否是正常的"无数据"情况（如当天数据还未生成）
            is_normal_empty = False
            
            # 检查元数据中是否有日期信息
            if 'date' in metric.metadata:
                target_date = metric.metadata['date']
                today = datetime.now()
                today_str = today.strftime("%Y%m%d")
                
                # 如果尝试拉取的是当天数据，且时间在15:30前，属于正常情况
                if target_date == today_str and today.time() < dt_time(15, 30):
                    is_normal_empty = True
                    log.info(f"[INFO] 当天数据 {target_date} 尚未生成（当前时间 {today.time().strftime('%H:%M:%S')} < 15:30），跳过告警")
                elif target_date > today_str:
                    # 未来日期，正常无数据
                    is_normal_empty = True
            
            if not is_normal_empty:
                alert = Alert(
                    timestamp=datetime.now().isoformat(),
                    level=AlertLevel.ERROR,
                    message=f"新增数据量为0，可能接口故障",
                    metric=metric,
                    threshold=self.alert_thresholds['min_new_rows'],
                    actual_value=0
                )
                alerts.append(alert)
        
        # 检查失败
        if not metric.success:
            alert = Alert(
                timestamp=datetime.now().isoformat(),
                level=AlertLevel.ERROR,
                message=f"操作失败: {metric.error_message}",
                metric=metric,
                threshold=0,
                actual_value=1
            )
            alerts.append(alert)
        
        # 检查总耗时
        if metric.duration_seconds > self.alert_thresholds['max_duration_total']:
            alert = Alert(
                timestamp=datetime.now().isoformat(),
                level=AlertLevel.CRITICAL,
                message=f"总任务耗时过长: {metric.duration_seconds:.2f}s > {self.alert_thresholds['max_duration_total']}s",
                metric=metric,
                threshold=self.alert_thresholds['max_duration_total'],
                actual_value=metric.duration_seconds
            )
            alerts.append(alert)
        
        # 触发告警
        for alert in alerts:
            self.alerts.append(alert)
            self.alert_callback(alert)
    
    def _default_alert_callback(self, alert: Alert):
        """默认告警回调"""
        log.warning(f"[{alert.level.value.upper()}] {alert.message}")
        
        # 如果启用飞书告警，发送飞书通知
        if self.enable_feishu_alert:
            self._send_feishu_alert(alert)
    
    def _send_feishu_alert(self, alert: Alert):
        """发送飞书告警通知"""
        try:
            from scripts.feishu_bot import send_error_notification
            
            # 构建告警消息
            alert_message = f"""⚠️ 数据拉取性能告警

触发时间：{alert.timestamp}
异常指标：{alert.message}
实际值：{alert.actual_value:.2f}
阈值：{alert.threshold:.2f}
影响范围：{alert.metric.operation}

建议排查：网络延迟 / Tushare服务限流 / 数据库性能 / 接口故障"""
            
            send_error_notification(alert_message)
            log.info("飞书告警通知已发送")
        except Exception as e:
            log.error(f"发送飞书告警失败: {e}")
    
    def get_statistics(self, operation: str = None, 
                     hours: int = 24) -> Dict:
        """
        获取统计信息
        
        Args:
            operation: 指定操作类型，None表示全部
            hours: 统计最近N小时的数据
            
        Returns:
            统计信息字典
        """
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        filtered_metrics = [
            m for m in self.metrics 
            if datetime.fromisoformat(m.timestamp) > cutoff_time
        ]
        
        if operation:
            filtered_metrics = [m for m in filtered_metrics if operation in m.operation]
        
        if not filtered_metrics:
            return {
                'count': 0,
                'avg_duration': 0,
                'avg_records_per_second': 0,
                'success_rate': 0,
                'error_count': 0
            }
        
        total_duration = sum(m.duration_seconds for m in filtered_metrics)
        total_records = sum(m.records_processed for m in filtered_metrics)
        success_count = sum(1 for m in filtered_metrics if m.success)
        
        return {
            'count': len(filtered_metrics),
            'avg_duration': total_duration / len(filtered_metrics),
            'avg_records_per_second': total_records / total_duration if total_duration > 0 else 0,
            'success_rate': success_count / len(filtered_metrics) * 100,
            'error_count': len(filtered_metrics) - success_count,
            'total_records': total_records
        }
    
    def get_recent_alerts(self, hours: int = 24) -> List[Alert]:
        """获取最近的告警"""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        return [
            a for a in self.alerts 
            if datetime.fromisoformat(a.timestamp) > cutoff_time
        ]
    
    def clear_old_metrics(self, hours: int = 168):  # 默认保留7天
        """清理旧的指标数据"""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        self.metrics = [
            m for m in self.metrics 
            if datetime.fromisoformat(m.timestamp) > cutoff_time
        ]
        log.info(f"清理了 {len(self.metrics)} 条旧指标数据")
    
    def export_metrics(self, filepath: str):
        """导出指标数据到文件"""
        data = {
            'metrics': [asdict(m) for m in self.metrics],
            'alerts': [asdict(a) for a in self.alerts],
            'export_time': datetime.now().isoformat()
        }
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        log.info(f"指标数据已导出到: {filepath}")


class PerformanceTimer:
    """性能计时器上下文管理器"""
    
    def __init__(self, monitor: PerformanceMonitor, operation: str, 
                 metadata: Dict = None):
        self.monitor = monitor
        self.operation = operation
        self.metadata = metadata or {}
        self.start_time = None
        self.records_processed = 0
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        success = exc_type is None
        error_message = str(exc_val) if exc_val else ""
        
        metric = PerformanceMetric(
            timestamp=datetime.now().isoformat(),
            operation=self.operation,
            duration_seconds=duration,
            records_processed=self.records_processed,
            success=success,
            error_message=error_message,
            metadata=self.metadata
        )
        
        self.monitor.record_metric(metric)
    
    def add_records(self, count: int):
        """增加处理的记录数"""
        self.records_processed += count


# 全局监控器实例
_global_monitor: Optional[PerformanceMonitor] = None

def get_monitor(enable_feishu_alert: bool = False) -> PerformanceMonitor:
    """获取全局监控器实例"""
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = PerformanceMonitor(enable_feishu_alert=enable_feishu_alert)
    return _global_monitor


def monitor_performance(operation: str, metadata: Dict = None):
    """
    性能监控装饰器
    
    Args:
        operation: 操作名称
        metadata: 额外的元数据
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            monitor = get_monitor()
            with PerformanceTimer(monitor, operation, metadata):
                result = func(*args, **kwargs)
                # 如果返回值包含记录数，自动添加到计时器
                if isinstance(result, dict) and 'new_rows' in result:
                    # 这里需要特殊处理，因为计时器已经退出
                    pass
                return result
        return wrapper
    return decorator


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.INFO)
    
    monitor = PerformanceMonitor()
    
    # 测试正常操作
    with PerformanceTimer(monitor, "test_operation", {"test": "data"}) as timer:
        time.sleep(0.1)
        timer.add_records(1000)
    
    # 测试慢操作
    with PerformanceTimer(monitor, "slow_operation") as timer:
        time.sleep(6)  # 超过阈值
        timer.add_records(100)
    
    # 测试失败操作
    try:
        with PerformanceTimer(monitor, "failed_operation") as timer:
            raise Exception("测试错误")
    except:
        pass
    
    # 获取统计信息
    stats = monitor.get_statistics()
    print(f"统计信息: {stats}")
    
    # 获取告警
    alerts = monitor.get_recent_alerts()
    print(f"告警数量: {len(alerts)}")
    for alert in alerts:
        print(f"  [{alert.level.value}] {alert.message}")

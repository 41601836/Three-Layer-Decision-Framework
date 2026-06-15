"""
重试机制工具模块
提供简易的重试机制，用于处理网络请求、API调用等可能失败的操作
"""

import time
import logging
from typing import Callable, Any, Optional, Type
from functools import wraps

from config.constants import OLLAMA_RETRY_COUNT, OLLAMA_RETRY_DELAY

log = logging.getLogger(__name__)


def retry_on_exception(
    max_retries: int = OLLAMA_RETRY_COUNT,
    delay: float = OLLAMA_RETRY_DELAY,
    exceptions: tuple = (Exception,),
    backoff_factor: float = 2.0,
    on_retry: Optional[Callable] = None
):
    """
    装饰器：在遇到指定异常时自动重试
    
    Args:
        max_retries: 最大重试次数
        delay: 初始延迟时间（秒）
        exceptions: 需要重试的异常类型
        backoff_factor: 退避因子，每次重试延迟时间乘以该因子
        on_retry: 重试时的回调函数
    
    Returns:
        装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            current_delay = delay
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt < max_retries:
                        log.warning(
                            f"{func.__name__} 执行失败 (尝试 {attempt + 1}/{max_retries + 1}): {e}. "
                            f"{current_delay:.1f}秒后重试..."
                        )
                        
                        if on_retry:
                            on_retry(attempt + 1, e)
                        
                        time.sleep(current_delay)
                        current_delay *= backoff_factor
                    else:
                        log.error(
                            f"{func.__name__} 执行失败，已达最大重试次数 {max_retries}: {e}"
                        )
            
            # 所有重试都失败后抛出最后一个异常
            if last_exception:
                raise last_exception
            
            return None
        
        return wrapper
    return decorator


def retry_ollama_call(func: Callable) -> Callable:
    """
    专门用于 Ollama API 调用的重试装饰器
    使用默认的重试配置
    
    Args:
        func: 需要重试的函数
    
    Returns:
        包装后的函数
    """
    return retry_on_exception(
        max_retries=OLLAMA_RETRY_COUNT,
        delay=OLLAMA_RETRY_DELAY,
        exceptions=(ConnectionError, TimeoutError, Exception),
        backoff_factor=1.5
    )(func)


def safe_call_with_fallback(
    func: Callable,
    fallback_value: Any = None,
    exceptions: tuple = (Exception,),
    log_error: bool = True
) -> Any:
    """
    安全调用函数，失败时返回备用值
    
    Args:
        func: 要调用的函数
        fallback_value: 失败时的备用值
        exceptions: 需要捕获的异常类型
        log_error: 是否记录错误日志
    
    Returns:
        函数执行结果或备用值
    """
    try:
        return func()
    except exceptions as e:
        if log_error:
            log.error(f"函数执行失败: {e}")
        return fallback_value


def exponential_backoff_retry(
    func: Callable,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: tuple = (Exception,)
) -> Any:
    """
    指数退避重试机制
    
    Args:
        func: 要执行的函数
        max_retries: 最大重试次数
        initial_delay: 初始延迟时间
        max_delay: 最大延迟时间
        exceptions: 需要捕获的异常类型
    
    Returns:
        函数执行结果
    """
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return func()
        except exceptions as e:
            last_exception = e
            
            if attempt < max_retries:
                delay = min(initial_delay * (2 ** attempt), max_delay)
                log.warning(
                    f"执行失败 (尝试 {attempt + 1}/{max_retries + 1}): {e}. "
                    f"{delay:.1f}秒后重试..."
                )
                time.sleep(delay)
    
    if last_exception:
        raise last_exception
    
    return None

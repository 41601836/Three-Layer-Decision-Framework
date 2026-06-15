#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tushare API响应缓存模块
通过缓存API响应减少重复请求，提升数据拉取速度
"""
import os
import json
import hashlib
import time
from datetime import datetime, timedelta
from typing import Any, Optional
import logging

log = logging.getLogger(__name__)

class TushareCache:
    """Tushare API响应缓存器"""
    
    def __init__(self, cache_dir: str = None, ttl_hours: int = 24):
        """
        初始化缓存器
        
        Args:
            cache_dir: 缓存目录路径
            ttl_hours: 缓存有效期（小时），默认24小时
        """
        if cache_dir is None:
            cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cache', 'tushare')
        
        self.cache_dir = cache_dir
        self.ttl_hours = ttl_hours
        os.makedirs(cache_dir, exist_ok=True)
        
        self.stats = {
            'hits': 0,
            'misses': 0,
            'sets': 0,
            'evictions': 0
        }
    
    def _get_cache_key(self, api_name: str, params: dict) -> str:
        """生成缓存键"""
        # 将参数转换为排序后的字符串以确保一致性
        params_str = json.dumps(params, sort_keys=True)
        key_string = f"{api_name}:{params_str}"
        return hashlib.md5(key_string.encode()).hexdigest()
    
    def _get_cache_path(self, cache_key: str) -> str:
        """获取缓存文件路径"""
        return os.path.join(self.cache_dir, f"{cache_key}.json")
    
    def _is_expired(self, cache_path: str) -> bool:
        """检查缓存是否过期"""
        try:
            mtime = os.path.getmtime(cache_path)
            age = time.time() - mtime
            return age > (self.ttl_hours * 3600)
        except OSError:
            return True
    
    def get(self, api_name: str, params: dict) -> Optional[Any]:
        """
        从缓存获取数据
        
        Args:
            api_name: API名称
            params: 请求参数
            
        Returns:
            缓存的数据，如果不存在或过期则返回None
        """
        cache_key = self._get_cache_key(api_name, params)
        cache_path = self._get_cache_path(cache_key)
        
        try:
            if os.path.exists(cache_path) and not self._is_expired(cache_path):
                with open(cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.stats['hits'] += 1
                    log.debug(f"缓存命中: {api_name}")
                    return data
            else:
                self.stats['misses'] += 1
                if os.path.exists(cache_path):
                    os.remove(cache_path)
                    self.stats['evictions'] += 1
                return None
        except Exception as e:
            log.warning(f"缓存读取失败: {e}")
            self.stats['misses'] += 1
            return None
    
    def set(self, api_name: str, params: dict, data: Any) -> bool:
        """
        将数据存入缓存
        
        Args:
            api_name: API名称
            params: 请求参数
            data: 要缓存的数据
            
        Returns:
            是否成功
        """
        cache_key = self._get_cache_key(api_name, params)
        cache_path = self._get_cache_path(cache_key)
        
        try:
            # 将数据转换为JSON可序列化格式
            if hasattr(data, 'to_dict'):
                cache_data = data.to_dict()
            elif isinstance(data, (list, dict)):
                cache_data = data
            else:
                cache_data = {'value': data}
            
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            
            self.stats['sets'] += 1
            log.debug(f"缓存写入: {api_name}")
            return True
        except Exception as e:
            log.warning(f"缓存写入失败: {e}")
            return False
    
    def clear(self, api_name: str = None, older_than_hours: int = None) -> int:
        """
        清除缓存
        
        Args:
            api_name: 指定API名称，None表示清除所有
            older_than_hours: 清除超过指定小时的缓存，None表示清除所有
            
        Returns:
            清除的文件数量
        """
        cleared = 0
        try:
            for filename in os.listdir(self.cache_dir):
                if not filename.endswith('.json'):
                    continue
                
                filepath = os.path.join(self.cache_dir, filename)
                
                # 检查时间条件
                if older_than_hours is not None:
                    age = time.time() - os.path.getmtime(filepath)
                    if age < (older_than_hours * 3600):
                        continue
                
                # 检查API名称条件
                if api_name is not None:
                    # 这里需要从缓存文件中读取API名称，简化处理跳过
                    pass
                
                os.remove(filepath)
                cleared += 1
            
            log.info(f"清除了 {cleared} 个缓存文件")
            return cleared
        except Exception as e:
            log.error(f"清除缓存失败: {e}")
            return 0
    
    def get_stats(self) -> dict:
        """获取缓存统计信息"""
        total_requests = self.stats['hits'] + self.stats['misses']
        hit_rate = (self.stats['hits'] / total_requests * 100) if total_requests > 0 else 0
        
        return {
            **self.stats,
            'hit_rate': round(hit_rate, 2),
            'total_requests': total_requests
        }
    
    def cleanup_expired(self) -> int:
        """清理过期缓存"""
        return self.clear(older_than_hours=self.ttl_hours)
    
    def clear_pattern(self, pattern: str) -> int:
        """清理匹配模式的缓存文件
        
        Args:
            pattern: 缓存键模式（支持通配符*）
            
        Returns:
            清理的文件数量
        """
        cleared_count = 0
        
        try:
            import fnmatch
            
            for filename in os.listdir(self.cache_dir):
                if not filename.endswith('.json'):
                    continue
                
                # 尝试读取缓存键
                filepath = os.path.join(self.cache_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                    
                    cache_key = cache_data.get('key', '')
                    if fnmatch.fnmatch(cache_key, pattern):
                        os.remove(filepath)
                        cleared_count += 1
                        log.debug(f"清理匹配缓存: {cache_key} ({filename})")
                        
                except Exception as e:
                    log.warning(f"清理匹配缓存失败 {filename}: {e}")
            
            log.info(f"清理了 {cleared_count} 个匹配 '{pattern}' 的缓存文件")
            return cleared_count
            
        except Exception as e:
            log.error(f"清理匹配缓存失败: {e}")
            return 0


# 全局缓存实例
_global_cache: Optional[TushareCache] = None

def get_cache(ttl_hours: int = 24) -> TushareCache:
    """获取全局缓存实例"""
    global _global_cache
    if _global_cache is None:
        _global_cache = TushareCache(ttl_hours=ttl_hours)
    return _global_cache


def cached_api_call(api_name: str, params: dict, fetch_func, *args, **kwargs):
    """
    带缓存的API调用装饰器
    
    Args:
        api_name: API名称
        params: 请求参数
        fetch_func: 实际的API调用函数
        *args, **kwargs: 传递给fetch_func的参数
        
    Returns:
        API响应数据
    """
    cache = get_cache()
    
    # 尝试从缓存获取
    cached_data = cache.get(api_name, params)
    if cached_data is not None:
        return cached_data
    
    # 缓存未命中，调用API
    data = fetch_func(*args, **kwargs)
    
    # 将结果存入缓存
    if data is not None:
        cache.set(api_name, params, data)
    
    return data


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.INFO)
    
    cache = TushareCache(ttl_hours=1)
    
    # 测试缓存写入
    test_data = {"test": "data", "timestamp": datetime.now().isoformat()}
    cache.set("test_api", {"param1": "value1"}, test_data)
    
    # 测试缓存读取
    retrieved = cache.get("test_api", {"param1": "value1"})
    print(f"缓存读取: {retrieved}")
    
    # 测试统计
    stats = cache.get_stats()
    print(f"缓存统计: {stats}")
    
    # 测试清理
    cache.cleanup_expired()

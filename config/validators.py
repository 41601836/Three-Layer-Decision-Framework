"""
输入参数校验和空值保护工具模块
提供统一的参数验证、空值检查、合法性校验功能
"""

import re
from typing import Any, Optional, List, Dict
from config.constants import (
    TS_CODE_PATTERN, TS_CODE_MIN_LENGTH,
    MIN_PRICE, MAX_PRICE, MIN_VOLUME, MAX_VOLUME
)


class ValidationError(Exception):
    """参数验证错误"""
    pass


def validate_ts_code(ts_code: str) -> str:
    """
    校验股票代码格式
    
    Args:
        ts_code: 股票代码 (如 "600519.SH" 或 "000001.SZ")
    
    Returns:
        标准化后的股票代码
    
    Raises:
        ValidationError: 股票代码格式不正确
    """
    if not ts_code or not isinstance(ts_code, str):
        raise ValidationError("股票代码不能为空")
    
    ts_code = ts_code.strip()
    
    if len(ts_code) < TS_CODE_MIN_LENGTH:
        raise ValidationError(f"股票代码长度不足{TS_CODE_MIN_LENGTH}位")
    
    # 标准化格式：确保有后缀
    if not re.match(r"\.\w+$", ts_code):
        # 尝试根据数字推断交易所
        if ts_code.startswith("6"):
            ts_code = f"{ts_code}.SH"
        elif ts_code.startswith(("0", "3")):
            ts_code = f"{ts_code}.SZ"
        else:
            raise ValidationError("无法确定股票交易所，请提供完整代码（如600519.SH）")
    
    # 验证格式
    if not re.match(TS_CODE_PATTERN, ts_code):
        raise ValidationError(f"股票代码格式不正确，应为{TS_CODE_PATTERN}")
    
    return ts_code


def validate_price(price: Any, param_name: str = "price") -> float:
    """
    校验价格参数
    
    Args:
        price: 价格值
        param_name: 参数名称（用于错误信息）
    
    Returns:
        标准化后的价格
    
    Raises:
        ValidationError: 价格超出合理范围
    """
    if price is None:
        raise ValidationError(f"{param_name}不能为空")
    
    try:
        price = float(price)
    except (ValueError, TypeError):
        raise ValidationError(f"{param_name}必须是数字")
    
    if price < MIN_PRICE or price > MAX_PRICE:
        raise ValidationError(f"{param_name}超出合理范围 [{MIN_PRICE}, {MAX_PRICE}]")
    
    return price


def validate_volume(volume: Any, param_name: str = "volume") -> float:
    """
    校验成交量参数
    
    Args:
        volume: 成交量值
        param_name: 参数名称（用于错误信息）
    
    Returns:
        标准化后的成交量
    
    Raises:
        ValidationError: 成交量超出合理范围
    """
    if volume is None:
        raise ValidationError(f"{param_name}不能为空")
    
    try:
        volume = float(volume)
    except (ValueError, TypeError):
        raise ValidationError(f"{param_name}必须是数字")
    
    if volume < MIN_VOLUME or volume > MAX_VOLUME:
        raise ValidationError(f"{param_name}超出合理范围 [{MIN_VOLUME}, {MAX_VOLUME}]")
    
    return volume


def validate_score(score: Any, param_name: str = "score", min_val: float = 0, max_val: float = 100) -> float:
    """
    校验评分参数
    
    Args:
        score: 评分值
        param_name: 参数名称（用于错误信息）
        min_val: 最小值
        max_val: 最大值
    
    Returns:
        标准化后的评分
    
    Raises:
        ValidationError: 评分超出合理范围
    """
    if score is None:
        raise ValidationError(f"{param_name}不能为空")
    
    try:
        score = float(score)
    except (ValueError, TypeError):
        raise ValidationError(f"{param_name}必须是数字")
    
    if score < min_val or score > max_val:
        raise ValidationError(f"{param_name}超出合理范围 [{min_val}, {max_val}]")
    
    return score


def validate_percentage(percentage: Any, param_name: str = "percentage") -> float:
    """
    校验百分比参数
    
    Args:
        percentage: 百分比值
        param_name: 参数名称（用于错误信息）
    
    Returns:
        标准化后的百分比
    
    Raises:
        ValidationError: 百分比超出合理范围
    """
    if percentage is None:
        raise ValidationError(f"{param_name}不能为空")
    
    try:
        percentage = float(percentage)
    except (ValueError, TypeError):
        raise ValidationError(f"{param_name}必须是数字")
    
    if percentage < -100 or percentage > 100:
        raise ValidationError(f"{param_name}超出合理范围 [-100%, 100%]")
    
    return percentage


def safe_get(data: Dict, key: str, default: Any = None) -> Any:
    """
    安全获取字典值，提供空值保护
    
    Args:
        data: 字典数据
        key: 键名
        default: 默认值
    
    Returns:
        字典中的值或默认值
    """
    if not data or not isinstance(data, dict):
        return default
    
    return data.get(key, default)


def safe_str(value: Any, default: str = "") -> str:
    """
    安全转换为字符串，提供空值保护
    
    Args:
        value: 任意值
        default: 默认值
    
    Returns:
        字符串值或默认值
    """
    if value is None:
        return default
    
    try:
        return str(value).strip()
    except Exception:
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    """
    安全转换为浮点数，提供空值保护
    
    Args:
        value: 任意值
        default: 默认值
    
    Returns:
        浮点数值或默认值
    """
    if value is None:
        return default
    
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """
    安全转换为整数，提供空值保护
    
    Args:
        value: 任意值
        default: 默认值
    
    Returns:
        整数值或默认值
    """
    if value is None:
        return default
    
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def validate_required_fields(data: Dict, required_fields: List[str]) -> None:
    """
    校验必需字段是否存在
    
    Args:
        data: 数据字典
        required_fields: 必需字段列表
    
    Raises:
        ValidationError: 缺少必需字段
    """
    if not data or not isinstance(data, dict):
        raise ValidationError("数据不能为空")
    
    missing_fields = [field for field in required_fields if field not in data or data[field] is None]
    
    if missing_fields:
        raise ValidationError(f"缺少必需字段: {', '.join(missing_fields)}")


def validate_date_format(date_str: str, format_pattern: str = "%Y%m%d") -> str:
    """
    校验日期格式
    
    Args:
        date_str: 日期字符串
        format_pattern: 日期格式
    
    Returns:
        标准化后的日期字符串
    
    Raises:
        ValidationError: 日期格式不正确
    """
    if not date_str:
        raise ValidationError("日期不能为空")
    
    from datetime import datetime
    
    try:
        datetime.strptime(date_str, format_pattern)
        return date_str
    except ValueError:
        raise ValidationError(f"日期格式不正确，应为 {format_pattern}")


def validate_string_length(text: str, min_length: int = 0, max_length: int = 1000, param_name: str = "text") -> str:
    """
    校验字符串长度
    
    Args:
        text: 字符串
        min_length: 最小长度
        max_length: 最大长度
        param_name: 参数名称
    
    Returns:
        标准化后的字符串
    
    Raises:
        ValidationError: 字符串长度超出范围
    """
    if text is None:
        raise ValidationError(f"{param_name}不能为空")
    
    text = str(text).strip()
    
    if len(text) < min_length:
        raise ValidationError(f"{param_name}长度不足{min_length}位")
    
    if len(text) > max_length:
        raise ValidationError(f"{param_name}长度超过{max_length}位")
    
    return text


def validate_list_length(items: List, min_length: int = 0, max_length: int = 100, param_name: str = "list") -> List:
    """
    校验列表长度
    
    Args:
        items: 列表
        min_length: 最小长度
        max_length: 最大长度
        param_name: 参数名称
    
    Returns:
        标准化后的列表
    
    Raises:
        ValidationError: 列表长度超出范围
    """
    if items is None:
        return []
    
    if not isinstance(items, list):
        raise ValidationError(f"{param_name}必须是列表")
    
    if len(items) < min_length:
        raise ValidationError(f"{param_name}长度不足{min_length}个")
    
    if len(items) > max_length:
        raise ValidationError(f"{param_name}长度超过{max_length}个")
    
    return items


def sanitize_filename(filename: str) -> str:
    """
    清理文件名，移除不安全字符
    
    Args:
        filename: 原始文件名
    
    Returns:
        清理后的安全文件名
    """
    if not filename:
        return "unnamed"
    
    # 移除不安全字符
    unsafe_chars = r'[<>:"/\\|?*]'
    filename = re.sub(unsafe_chars, '_', filename)
    
    # 移除首尾空格和点
    filename = filename.strip('. ')
    
    # 限制长度
    if len(filename) > 200:
        filename = filename[:200]
    
    return filename or "unnamed"

import os
from typing import Any, Dict, Iterable


def deep_inject_env(data: Any) -> Any:
    """递归替换 ${ENV} 占位符为环境变量。"""
    if isinstance(data, dict):
        # 对字典逐项递归
        return {key: deep_inject_env(value) for key, value in data.items()}
    if isinstance(data, list):
        # 对列表逐项递归
        return [deep_inject_env(item) for item in data]
    if isinstance(data, tuple):
        # 对元组逐项递归并保持元组类型
        return tuple(deep_inject_env(item) for item in data)
    if isinstance(data, str) and data.startswith("${") and data.endswith("}"):
        # 字符串占位符：用环境变量值替换，若无则保留原文
        env_key = data[2:-1]
        return os.getenv(env_key, data)
    # 其它类型保持原样
    return data


def build_cookie_header(*, cookie_pairs: Dict[str, str], preferred_keys: Iterable[str]) -> str:
    """构建 Cookie header 字符串，优先输出指定 key，随后补齐其它 cookie。"""
    ordered = []
    used = set()
    for key in preferred_keys:
        value = cookie_pairs.get(key)
        if value:
            ordered.append(f"{key}={value}")
            used.add(key)
    for key, value in cookie_pairs.items():
        if key in used or value is None:
            continue
        ordered.append(f"{key}={value}")
    return "; ".join(ordered)


def get_nested_value(payload: Dict[str, Any], path: Iterable[str]) -> Any:
    """安全读取嵌套字典值，路径不存在时返回 None。"""
    current: Any = payload
    for key in path:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current

import os
from typing import Any, Dict, Iterable


def deep_inject_env(data: Any) -> Any:
    """递归替换 ${ENV} 占位符为环境变量。"""
    if isinstance(data, dict):
        return {key: deep_inject_env(value) for key, value in data.items()}
    if isinstance(data, list):
        return [deep_inject_env(item) for item in data]
    if isinstance(data, tuple):
        return tuple(deep_inject_env(item) for item in data)
    if isinstance(data, str) and data.startswith("${") and data.endswith("}"):
        env_key = data[2:-1]
        return os.getenv(env_key, data)
    return data


def build_cookie_header(*, cookie_pairs: Dict[str, str], preferred_keys: Iterable[str]) -> str:
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
    current: Any = payload
    for key in path:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current

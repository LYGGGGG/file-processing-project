import os
from typing import Any, Dict, Iterable, Optional


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


def parse_cookie_header(cookie_header: str) -> Dict[str, str]:
    """解析 Cookie header 为字典。"""
    cookies: Dict[str, str] = {}
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        cookies[key.strip()] = value.strip()
    return cookies


def normalize_auth_headers(
    headers: Dict[str, str],
    *,
    cookie_key: str = "cookie",
    auth_key: str = "auth_token",
    env_key: str = "AUTH_TOKEN",
) -> Optional[str]:
    """确保 headers 中写入 auth_token，并返回最终 token。"""
    current = headers.get(auth_key)
    if isinstance(current, str) and current.startswith("${") and current.endswith("}"):
        headers.pop(auth_key, None)
        current = None
    if current:
        return current
    cookie_header = headers.get(cookie_key, "") or ""
    cookie_token = parse_cookie_header(cookie_header).get("AUTH_TOKEN")
    env_token = os.getenv(env_key)
    token = cookie_token or env_token
    if token:
        headers[auth_key] = token
    return token


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

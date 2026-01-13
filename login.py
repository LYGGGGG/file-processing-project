"""登录辅助：仅用于获取 cookie/token。"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Optional

import ddddocr
import requests

logger = logging.getLogger(__name__)


def _deep_inject_env(value: Any) -> Any:
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            env_key = value[2:-1]
            return os.getenv(env_key, value)
        return value
    if isinstance(value, list):
        return [_deep_inject_env(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_deep_inject_env(item) for item in value)
    if isinstance(value, dict):
        return {key: _deep_inject_env(item) for key, item in value.items()}
    return value


def _md5_hex(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def _decode_base64_image(raw: str) -> bytes:
    match = re.match(r"data:image/(\w+);base64,(.*)", raw)
    data = match.group(2) if match else raw
    return base64.b64decode(data)


def _fetch_captcha(session: requests.Session, config: Mapping[str, Any]) -> Optional[Dict[str, str]]:
    if not config.get("enabled", False):
        return None

    value_env_key = config.get("value_env_key")
    key_env_key = config.get("key_env_key")
    rs_id_env_key = config.get("rs_id_env_key")

    env_value = os.getenv(value_env_key) if value_env_key else None
    if env_value:
        return {
            "value": env_value,
            "key": os.getenv(key_env_key) if key_env_key else "",
            "rs_id": os.getenv(rs_id_env_key) if rs_id_env_key else "",
        }

    url = config.get("url")
    if not url:
        raise ValueError("captcha.url 未配置")

    headers = _deep_inject_env(config.get("headers", {}))
    params = _deep_inject_env(config.get("params", {}))
    timeout = int(config.get("timeout", 10))

    response = session.request(config.get("method", "GET"), url, headers=headers, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()

    image_field = config.get("image_field", "randomCodeImage")
    raw_image = payload.get(image_field, "")
    if not raw_image:
        raise ValueError("验证码响应缺少图片字段")

    image_bytes = _decode_base64_image(raw_image)

    save_path = config.get("save_path")
    if save_path:
        path_obj = Path(save_path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        path_obj.write_bytes(image_bytes)

    ocr = ddddocr.DdddOcr()
    value = ocr.classification(image_bytes)

    return {
        "value": value,
        "key": payload.get(config.get("key_field", "captchaKey"), ""),
        "rs_id": payload.get(config.get("rs_id_field", "_rs_id"), ""),
    }


def _build_cookie_string(cookiejar: requests.cookies.RequestsCookieJar) -> str:
    cookies = requests.utils.dict_from_cookiejar(cookiejar)
    return "; ".join([f"{key}={value}" for key, value in cookies.items()])


def _apply_auth_to_headers(headers: MutableMapping[str, str], token: str, cookie: str) -> None:
    for key, value in list(headers.items()):
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_key = value[2:-1]
            if env_key == "AUTH_TOKEN":
                headers[key] = token
            elif env_key == "COOKIE":
                headers[key] = cookie


def login_and_refresh_auth(config: MutableMapping[str, Any]) -> Optional[Dict[str, str]]:
    login_cfg = config.get("login_api")
    if not isinstance(login_cfg, Mapping) or not login_cfg.get("enabled", False):
        return None

    session = requests.Session()

    captcha_cfg = login_cfg.get("captcha", {})
    captcha_result = _fetch_captcha(session, captcha_cfg)

    login_conf = login_cfg.get("login", {})
    url = login_conf.get("url")
    if not url:
        raise ValueError("login.url 未配置")

    headers = _deep_inject_env(login_conf.get("headers", {}))
    params = _deep_inject_env(login_conf.get("params_template", {}))
    payload = _deep_inject_env(login_conf.get("payload_template", {}))

    if captcha_result:
        payload[login_conf.get("captcha_field", "captcha")] = captcha_result["value"]
        captcha_key_field = login_conf.get("captcha_key_field")
        if captcha_key_field and captcha_result.get("key"):
            payload[captcha_key_field] = captcha_result["key"]

        rs_id = captcha_result.get("rs_id")
        if rs_id:
            params[login_conf.get("rs_id_param", "_rs_id")] = rs_id
        params[login_conf.get("random_code_param", "_randomCode_")] = captcha_result["value"]

    if login_conf.get("password_hash") == "md5" and "password" in payload:
        payload["password"] = _md5_hex(str(payload["password"]))

    timeout = int(login_conf.get("timeout", 15))
    response = session.request(
        login_conf.get("method", "POST"),
        url,
        headers=headers,
        params=params,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()

    response_data = response.json()
    token_path = login_cfg.get("token_json_path", ["data", "token"])
    token: Optional[Any] = response_data
    for key in token_path:
        if not isinstance(token, Mapping) or key not in token:
            token = None
            break
        token = token[key]
    if token is None:
        raise ValueError("登录响应未返回 token")

    cookie_string = _build_cookie_string(session.cookies)

    token_env = login_cfg.get("token_env", "AUTH_TOKEN")
    cookie_env = login_cfg.get("cookie_env", "COOKIE")
    os.environ[token_env] = str(token)
    os.environ[cookie_env] = cookie_string

    for section in ("list_api", "export_api"):
        section_cfg = config.get(section)
        if not isinstance(section_cfg, Mapping):
            continue
        headers_section = section_cfg.get("headers")
        if isinstance(headers_section, MutableMapping):
            _apply_auth_to_headers(headers_section, str(token), cookie_string)

    logger.info("登录成功，已刷新 AUTH_TOKEN 与 COOKIE")
    return {"auth_token": str(token), "cookie": cookie_string}

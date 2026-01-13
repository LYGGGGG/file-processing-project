# -*- coding: utf-8 -*-
import base64
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import ddddocr
import requests

from utils import build_cookie_header, deep_inject_env, get_nested_value

logger = logging.getLogger(__name__)


@dataclass
class LoginResult:
    auth_token: str
    cookie_header: str
    raw_cookies: Dict[str, str]


def _hash_password(password: str, method: str) -> str:
    if method == "md5":
        return hashlib.md5(password.encode("utf-8")).hexdigest()
    if method == "md5_digest":
        return hashlib.md5(password.encode("utf-8")).digest().decode("latin1")
    raise ValueError(f"Unsupported password hash method: {method}")


def _request_captcha(config: Dict[str, Any]) -> Tuple[str, str]:
    captcha_cfg = config["captcha"]
    headers = deep_inject_env(captcha_cfg.get("headers", {}))
    params = deep_inject_env(captcha_cfg.get("params", {}))
    url = captcha_cfg["url"]
    timeout = captcha_cfg.get("timeout", 10)
    retries = captcha_cfg.get("retries", 3)
    retry_sleep = captcha_cfg.get("retry_sleep", 1)

    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            image_data = data[captcha_cfg["image_field"]]
            rs_id = data[captcha_cfg["rs_id_field"]]

            match = re.match(r"data:image/(\\w+);base64,(.*)", image_data)
            if not match:
                raise ValueError("验证码图片格式解析失败")

            image_bytes = base64.b64decode(match.group(2))
            ocr = ddddocr.DdddOcr()
            captcha_value = ocr.classification(image_bytes)
            return rs_id, captcha_value
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("验证码请求失败（第 %s/%s 次）：%s", attempt, retries, exc)
            time.sleep(retry_sleep)

    raise last_exc  # type: ignore[misc]


def _build_login_payload(config: Dict[str, Any], rs_id: Optional[str], captcha_value: Optional[str]) -> Dict[str, Any]:
    login_cfg = config["login"]
    payload = deep_inject_env(login_cfg.get("payload_template", {}))
    if login_cfg.get("password_hash") == "md5":
        password = payload.get("password", "")
        payload["password"] = _hash_password(password, "md5")
    if captcha_value:
        payload[login_cfg.get("captcha_field", "captcha")] = captcha_value
    if rs_id and login_cfg.get("rs_id_param"):
        payload[login_cfg["rs_id_param"]] = rs_id
    return payload


def _login_request(config: Dict[str, Any], rs_id: Optional[str], captcha_value: Optional[str]) -> requests.Response:
    login_cfg = config["login"]
    headers = deep_inject_env(login_cfg.get("headers", {}))
    params = deep_inject_env(login_cfg.get("params_template", {}))
    if rs_id and login_cfg.get("rs_id_param"):
        params[login_cfg["rs_id_param"]] = rs_id
    if captcha_value and login_cfg.get("random_code_param"):
        params[login_cfg["random_code_param"]] = captcha_value

    payload = _build_login_payload(config, rs_id, captcha_value)
    url = login_cfg["url"]
    timeout = login_cfg.get("timeout", 15)
    return requests.post(url, headers=headers, params=params, json=payload, timeout=timeout)


def _extract_auth_token(response: requests.Response, config: Dict[str, Any]) -> str:
    token_path = config.get("token_json_path", ["data"])
    data = response.json()
    token = get_nested_value(data, token_path)
    if not token:
        raise ValueError("登录响应未返回 AUTH_TOKEN")
    return str(token)


def login_and_fetch_cookie(config: Dict[str, Any]) -> LoginResult:
    captcha_cfg = config.get("captcha", {})
    captcha_enabled = captcha_cfg.get("enabled", True)

    rs_id = None
    captcha_value = None
    if captcha_enabled:
        rs_id, captcha_value = _request_captcha(config)
    else:
        captcha_value = os.getenv(captcha_cfg.get("value_env_key", ""), None)
        rs_id = os.getenv(captcha_cfg.get("rs_id_env_key", ""), None)

    response = _login_request(config, rs_id, captcha_value)
    response.raise_for_status()

    auth_token = _extract_auth_token(response, config)
    cookie_pairs = requests.utils.dict_from_cookiejar(response.cookies)
    cookie_pairs["AUTH_TOKEN"] = auth_token
    cookie_header = build_cookie_header(
        cookie_pairs=cookie_pairs,
        preferred_keys=["IGNORE-SESSION", "AUTH_TOKEN", "BGWL-EXEC-PROD", "HWWAFSESTIME", "HWWAFSESID"],
    )

    return LoginResult(auth_token=auth_token, cookie_header=cookie_header, raw_cookies=cookie_pairs)


def login_and_refresh_auth(config: Dict[str, Any]) -> LoginResult:
    login_cfg = config.get("login_api", {})
    if not login_cfg.get("enabled", False):
        raise RuntimeError("login_api 未启用，无法自动登录")

    result = login_and_fetch_cookie(login_cfg)
    os.environ[login_cfg.get("token_env", "AUTH_TOKEN")] = result.auth_token
    os.environ[login_cfg.get("cookie_env", "COOKIE")] = result.cookie_header
    logger.info("已刷新登录鉴权，AUTH_TOKEN/Cookie 已更新")
    return result


__all__ = ["login_and_refresh_auth", "login_and_fetch_cookie", "LoginResult"]

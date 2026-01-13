"""登录与鉴权辅助逻辑。"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional

import ddddocr
import requests

logger = logging.getLogger(__name__)


@dataclass
class CaptchaResult:
    value: str
    key: Optional[str]
    rs_id: Optional[str]
    image_bytes: Optional[bytes]


def _is_env_placeholder(value: str) -> bool:
    return value.startswith("${") and value.endswith("}")


def _resolve_env_placeholder(value: str) -> str:
    if _is_env_placeholder(value):
        env_key = value[2:-1]
        return os.getenv(env_key, value)
    return value


def _deep_inject_env(value: Any) -> Any:
    if isinstance(value, str):
        return _resolve_env_placeholder(value)
    if isinstance(value, list):
        return [_deep_inject_env(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_deep_inject_env(item) for item in value)
    if isinstance(value, dict):
        return {key: _deep_inject_env(item) for key, item in value.items()}
    return value


def _md5_hex(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def _extract_json_path(data: Mapping[str, Any], path: Iterable[str]) -> Optional[Any]:
    current: Any = data
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _request_with_retry(
    session: requests.Session,
    *,
    method: str,
    url: str,
    retries: int,
    timeout: int,
    headers: Optional[Mapping[str, str]] = None,
    params: Optional[Mapping[str, Any]] = None,
    json_payload: Optional[Mapping[str, Any]] = None,
) -> requests.Response:
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            response = session.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_payload,
                timeout=timeout,
            )
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("请求失败（第 %s/%s 次）：%s", attempt, retries, exc)
    raise last_exc  # type: ignore[misc]


def _decode_base64_image(raw: str) -> bytes:
    match = re.match(r"data:image/(\w+);base64,(.*)", raw)
    data = match.group(2) if match else raw
    return base64.b64decode(data)


def _fetch_captcha(
    session: requests.Session,
    config: Mapping[str, Any],
) -> Optional[CaptchaResult]:
    if not config.get("enabled", False):
        return None

    value_env_key = config.get("value_env_key")
    key_env_key = config.get("key_env_key")
    rs_id_env_key = config.get("rs_id_env_key")

    env_value = os.getenv(value_env_key) if value_env_key else None
    env_key = os.getenv(key_env_key) if key_env_key else None
    env_rs_id = os.getenv(rs_id_env_key) if rs_id_env_key else None

    if env_value:
        return CaptchaResult(value=env_value, key=env_key, rs_id=env_rs_id, image_bytes=None)

    url = config.get("url")
    if not url:
        raise ValueError("captcha.url 未配置")

    headers = _deep_inject_env(config.get("headers", {}))
    params = _deep_inject_env(config.get("params", {}))
    retries = int(config.get("retries", 1))
    timeout = int(config.get("timeout", 10))

    response = _request_with_retry(
        session,
        method=config.get("method", "GET"),
        url=url,
        retries=retries,
        timeout=timeout,
        headers=headers,
        params=params,
    )
    payload = response.json()

    image_field = config.get("image_field", "randomCodeImage")
    key_field = config.get("key_field", "captchaKey")
    rs_id_field = config.get("rs_id_field", "_rs_id")

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

    return CaptchaResult(
        value=value,
        key=payload.get(key_field),
        rs_id=payload.get(rs_id_field),
        image_bytes=image_bytes,
    )


def _build_cookie_string(cookiejar: requests.cookies.RequestsCookieJar) -> str:
    cookies = requests.utils.dict_from_cookiejar(cookiejar)
    return "; ".join([f"{key}={value}" for key, value in cookies.items()])


def _apply_auth_to_headers(headers: MutableMapping[str, str], token: str, cookie: str) -> None:
    for key, value in list(headers.items()):
        if isinstance(value, str) and _is_env_placeholder(value):
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
        captcha_field = login_conf.get("captcha_field", "captcha")
        payload[captcha_field] = captcha_result.value
        captcha_key_field = login_conf.get("captcha_key_field")
        if captcha_key_field and captcha_result.key:
            payload[captcha_key_field] = captcha_result.key

        if captcha_result.rs_id:
            rs_id_param = login_conf.get("rs_id_param", "_rs_id")
            params[rs_id_param] = captcha_result.rs_id
        random_code_param = login_conf.get("random_code_param", "_randomCode_")
        params[random_code_param] = captcha_result.value

    password_hash = login_conf.get("password_hash")
    if password_hash == "md5" and "password" in payload:
        payload["password"] = _md5_hex(str(payload["password"]))

    retries = int(login_conf.get("retries", 1))
    timeout = int(login_conf.get("timeout", 15))

    response = _request_with_retry(
        session,
        method=login_conf.get("method", "POST"),
        url=url,
        retries=retries,
        timeout=timeout,
        headers=headers,
        params=params,
        json_payload=payload,
    )

    response_data = response.json()
    token_path = login_cfg.get("token_json_path", ["data", "token"])
    token = _extract_json_path(response_data, token_path)
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

    logger.info("登录成功，AUTH_TOKEN 与 COOKIE 已刷新")
    return {"auth_token": str(token), "cookie": cookie_string}

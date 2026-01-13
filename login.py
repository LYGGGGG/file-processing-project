import base64
import json
import logging
import os
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import requests
import ddddocr
from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_OCR = ddddocr.DdddOcr(show_ad=False)


def _inject_env(value: Any) -> Any:
    """把形如 ${AUTH_TOKEN} 的字符串替换成环境变量值；其他类型原样返回。"""
    if not isinstance(value, str):
        return value
    if value.startswith("${") and value.endswith("}"):
        key = value[2:-1]
        return os.getenv(key, "")
    return value


def _deep_inject_env(obj: Any) -> Any:
    """递归替换 dict/list 中的 ${VAR} 占位符。"""
    if isinstance(obj, dict):
        return {k: _deep_inject_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_inject_env(x) for x in obj]
    return _inject_env(obj)


def _extract_json_path(data: Dict[str, Any], path: Iterable[str]) -> Optional[Any]:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _cookies_to_header(session: requests.Session) -> str:
    return "; ".join([f"{c.name}={c.value}" for c in session.cookies])


def _preprocess_captcha(image_bytes: bytes) -> Image.Image:
    image = Image.open(BytesIO(image_bytes)).convert("L")
    image = ImageOps.invert(image)
    image = image.filter(ImageFilter.MedianFilter())
    image = image.point(lambda x: 0 if x < 140 else 255)
    return image


def _recognize_captcha(image_bytes: bytes) -> str:
    image = _preprocess_captcha(image_bytes)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    text = _OCR.classification(buffer.getvalue())
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits


def _strip_data_url(image_data: str) -> str:
    if image_data.startswith("data:"):
        return image_data.split(",", 1)[1] if "," in image_data else ""
    return image_data


def _save_captcha_image(image_bytes: bytes, save_path: str) -> None:
    path = Path(save_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image_bytes)


def _build_request_url(method: str, url: str, params: Optional[Dict[str, Any]]) -> str:
    if not params:
        return url
    req = requests.Request(method=method, url=url, params=params)
    prepared = req.prepare()
    return prepared.url


def _request_captcha(
    session: requests.Session,
    captcha_cfg: Dict[str, Any],
) -> Tuple[bytes, Optional[str], Optional[str]]:
    method = captcha_cfg.get("method", "GET").upper()
    url = captcha_cfg["url"]
    headers = _deep_inject_env(captcha_cfg.get("headers", {}))
    params = _deep_inject_env(captcha_cfg.get("params", {}))
    payload = _deep_inject_env(captcha_cfg.get("payload", {}))
    retries = int(captcha_cfg.get("retries", 0))
    retry_sleep = float(captcha_cfg.get("retry_sleep", 1))
    full_url = _build_request_url(method, url, params)
    logger.info("验证码接口 URL：%s", full_url)

    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        if method == "POST":
            logger.info("验证码请求方式：POST，payload=%s", payload)
            resp = session.post(
                url,
                headers=headers,
                params=params,
                json=payload,
                timeout=captcha_cfg.get("timeout", 10),
            )
        else:
            logger.info("验证码请求方式：GET")
            resp = session.get(
                url,
                headers=headers,
                params=params,
                timeout=captcha_cfg.get("timeout", 10),
            )

        if resp.status_code == 409:
            logger.warning(
                "验证码请求冲突(409)，第 %s/%s 次：%s",
                attempt + 1,
                retries + 1,
                resp.text,
            )
            if attempt < retries:
                time.sleep(retry_sleep)
                continue
        try:
            resp.raise_for_status()
            last_exc = None
            break
        except requests.HTTPError as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(retry_sleep)
                continue
            raise

    if last_exc:
        raise last_exc
    response_type = captcha_cfg.get("response_type", "binary")
    captcha_key = None
    captcha_rs_id = None

    if response_type == "base64_json":
        payload_json = resp.json()
        image_field = captcha_cfg.get("image_field", "data")
        image_b64 = _strip_data_url(payload_json.get(image_field, ""))
        if not image_b64:
            raise ValueError("验证码 base64 数据为空")
        image_bytes = base64.b64decode(image_b64)
        save_path = captcha_cfg.get("save_path", "")
        if save_path:
            _save_captcha_image(image_bytes, save_path)
        key_field = captcha_cfg.get("key_field")
        if key_field:
            captcha_key = payload_json.get(key_field)
        rs_id_field = captcha_cfg.get("rs_id_field")
        if rs_id_field:
            captcha_rs_id = payload_json.get(rs_id_field)
        return image_bytes, captcha_key, captcha_rs_id

    return resp.content, None, None


def login_and_refresh_auth(config: Dict[str, Any]) -> Dict[str, str]:
    login_cfg = config.get("login_api", {})
    if not login_cfg.get("enabled", False):
        logger.info("login_api 未启用，跳过登录流程")
        return {}

    session = requests.Session()
    captcha_cfg = login_cfg["captcha"]
    captcha_value = ""
    captcha_key = None
    captcha_rs_id = None

    value_env_key = captcha_cfg.get("value_env_key", "CAPTCHA_VALUE")
    key_env_key = captcha_cfg.get("key_env_key", "")
    rs_id_env_key = captcha_cfg.get("rs_id_env_key", "")
    if captcha_cfg.get("enabled", True):
        image_bytes, captcha_key, captcha_rs_id = _request_captcha(session, captcha_cfg)
        captcha_value = _recognize_captcha(image_bytes)
        if not captcha_value:
            captcha_value = os.getenv(value_env_key, "")
            if not captcha_value:
                save_path = captcha_cfg.get("save_path", "")
                raise ValueError(
                    "验证码识别失败。请设置 "
                    f"{value_env_key}（手工输入验证码）后重试。"
                    f"验证码图片已保存至 {save_path}。"
                )
    else:
        captcha_value = os.getenv(value_env_key, "")

        if not captcha_value:
            raise ValueError(
                f"验证码请求已关闭，但 {value_env_key} 为空；"
                "请设置验证码值或启用验证码请求。"
            )

    if key_env_key and not captcha_key:
        captcha_key = os.getenv(key_env_key, None)
    if rs_id_env_key and not captcha_rs_id:
        captcha_rs_id = os.getenv(rs_id_env_key, None)

    if not captcha_value:
        raise ValueError("验证码识别失败，结果为空")

    logger.info("验证码识别完成：%s", captcha_value)

    login_request = login_cfg["login"]
    login_method = login_request.get("method", "POST").upper()
    login_url = login_request["url"]
    login_headers = _deep_inject_env(login_request.get("headers", {}))
    login_params = _deep_inject_env(login_request.get("params_template", {}))
    login_full_url = _build_request_url(login_method, login_url, login_params)
    logger.info("登录接口 URL：%s", login_full_url)

    if captcha_rs_id:
        login_params.setdefault(login_request.get("rs_id_param", "_rs_id"), captcha_rs_id)
    if captcha_value:
        login_params.setdefault(login_request.get("random_code_param", "_randomCode_"), captcha_value)
    payload_template = _deep_inject_env(login_request.get("payload_template", {}))

    captcha_field = login_request.get("captcha_field", "captcha")
    payload_template[captcha_field] = captcha_value
    if captcha_key and login_request.get("captcha_key_field"):
        payload_template[login_request["captcha_key_field"]] = captcha_key

    if login_method == "POST":
        logger.info("登录请求方式：POST，参数=%s，payload=%s", login_params, payload_template)
        resp = session.post(
            login_url,
            headers=login_headers,
            params=login_params,
            json=payload_template,
            timeout=login_request.get("timeout", 15),
        )
    else:
        merged_params = {**login_params, **payload_template}
        login_full_url = _build_request_url(login_method, login_url, merged_params)
        logger.info("登录请求方式：GET，完整 URL：%s", login_full_url)
        resp = session.get(
            login_url,
            headers=login_headers,
            params=merged_params,
            timeout=login_request.get("timeout", 15),
        )
    resp.raise_for_status()

    token = None
    token_path = login_cfg.get("token_json_path", [])
    if token_path:
        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            raise ValueError("登录接口返回值不是 JSON") from exc
        token = _extract_json_path(payload, token_path)

    cookie_header = _cookies_to_header(session)
    updates: Dict[str, str] = {}

    if token:
        token_env = login_cfg.get("token_env", "AUTH_TOKEN")
        os.environ[token_env] = str(token)
        updates[token_env] = str(token)
        logger.info("登录获取 token：%s=%s", token_env, token)

    if cookie_header:
        cookie_env = login_cfg.get("cookie_env", "COOKIE")
        os.environ[cookie_env] = cookie_header
        updates[cookie_env] = cookie_header
        logger.info("登录获取 cookie：%s=%s", cookie_env, cookie_header)

    logger.info("登录完成，更新环境变量：%s", ",".join(updates.keys()))
    return updates

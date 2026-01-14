# -*- coding: utf-8 -*-
import base64
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

import ddddocr
import requests


# 模块级日志器：输出验证码与登录流程日志
logger = logging.getLogger(__name__)

# 验证码缓存有效期（秒），用于减少重复请求验证码接口
CAPTCHA_CACHE_TTL_SECONDS = int(os.getenv("CAPTCHA_TTL_SECONDS", "60"))
# 缓存结构: rs_id/code/path/fetched_at
_CAPTCHA_CACHE: dict = {}


def _get_cached_captcha() -> Optional[Tuple[str, str, Optional[Path]]]:
    """读取验证码缓存，若超时则返回 None。"""
    if not _CAPTCHA_CACHE:
        return None
    fetched_at = _CAPTCHA_CACHE.get("fetched_at")
    if fetched_at is None:
        return None
    age = time.time() - fetched_at
    if age >= CAPTCHA_CACHE_TTL_SECONDS:
        # 超过 TTL 则丢弃缓存
        logger.info("验证码缓存已过期: age=%.2fs ttl=%ss", age, CAPTCHA_CACHE_TTL_SECONDS)
        return None
    logger.info("使用缓存验证码: age=%.2fs ttl=%ss", age, CAPTCHA_CACHE_TTL_SECONDS)
    return (
        _CAPTCHA_CACHE.get("rs_id", ""),
        _CAPTCHA_CACHE.get("code", ""),
        _CAPTCHA_CACHE.get("path"),
    )


def get_captcha_data(*, force_refresh: bool = False) -> Tuple[str, str, Optional[Path]]:
    """读取验证码数据，必要时重新请求并更新缓存。"""
    if not force_refresh:
        cached = _get_cached_captcha()
        if cached:
            return cached
    # 强制刷新或缓存失效时重新请求验证码
    rs_id, code, path = fetch_captcha()
    _CAPTCHA_CACHE.clear()
    _CAPTCHA_CACHE.update(
        {
            "rs_id": rs_id,
            "code": code,
            "path": path,
            "fetched_at": time.time(),
        }
    )
    return rs_id, code, path


def fetch_captcha() -> Tuple[str, str, Optional[Path]]:
    """获取验证码信息并保存图片，返回(rs_id, 识别码, 保存路径)。"""
    show_value = int(time.time() * 1000)
    url = f"https://bgwlgl.bbwport.com/api/bgwl-cloud-center/random?show={show_value}"
    try:
        logger.info("验证码请求 URL: %s", url)
        response = requests.get(url, headers={"cache-control": "no-cache"})
        response.raise_for_status()
        data = response.json()
        logger.info(
            "验证码响应状态=%s, content-type=%s, keys=%s",
            response.status_code,
            response.headers.get("content-type"),
            list(data.keys()),
        )
        rs_id = data["_rs_id"]
        # 保存图片
        image_data = data["randomCodeImage"]
        match = re.match(r"data:image/(\w+);base64,(.*)", image_data)

        if match:
            img_format = match.group(1)
            img_data = match.group(2)
            # 直接解码base64数据，避免写入临时文件
            image_bytes = base64.b64decode(img_data)

            # 直接使用解码后的数据进行OCR识别
            ocr = ddddocr.DdddOcr()
            text = ocr.classification(image_bytes)
            logger.info("验证码识别结果: %s (rs_id=%s)", text, rs_id)
            save_dir = Path("data") / "captcha"
            save_dir.mkdir(parents=True, exist_ok=True)
            save_path = save_dir / "captcha.png"
            save_path.write_bytes(image_bytes)
            logger.info("验证码图片已保存: %s (bytes=%s)", save_path, len(image_bytes))
            # 返回识别文本与文件路径
            return rs_id, text, save_path
        logger.warning("验证码图片解析失败，原始数据格式不符合预期。")
        return rs_id, "", None
        # 保存rs_id
        # with open("rs_id.txt", "w") as f:
        #     f.write(data["_rs_id"])
    except Exception as e:
        logger.exception("获取验证码失败: %s", e)
        raise


"""工厂函数生成指定输出格式的MD5计算函数"""


def create_output_method(method: str) -> Callable[[str], str]:
    """生成指定输出格式的 MD5 计算函数。"""
    def hash_func(input_str: str) -> str:
        # 按 UTF-8 编码计算 MD5
        md5 = hashlib.md5(input_str.encode('utf-8'))
        if method == 'hexdigest':
            return md5.hexdigest()
        elif method == 'digest':
            return md5.digest().decode('latin1')
        raise ValueError(f"Unsupported method: {method}")

    return hash_func


# 获取cookie
def login_with_captcha():
    """请求验证码并执行登录，返回登录响应与 session。"""
    captcha = get_captcha_data()
    rs_id = captcha[0]
    code = captcha[1]
    logger.info("登录使用验证码: rs_id=%s code=%s", rs_id, code)
    md5_hex = create_output_method('hexdigest')
    # 登录 payload 使用 MD5 加密后的固定密码示例
    payload = {
        "rsid": rs_id,
        "code": code,
        "password": md5_hex('abc1234.'),
        "username": "梁易高",
    }

    payload_json = json.dumps(payload)
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json;charset=UTF-8",
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
    }
    session = requests.Session()
    # 1. 首先执行登录 POST 请求
    url = f"https://bgwlgl.bbwport.com/api/bgwl-cloud-center/login.do?_rs_id={rs_id}&_randomCode_={code}"
    login_response = session.post(url, headers=headers, data=payload_json)
    # print(login_response.json()['data'])

    return login_response, session


def build_login_cookies():
    """校验登录响应中的 cookie 与 AUTH_TOKEN，返回可用 cookie 字典。"""
    login_result = login_with_captcha()

    # 从登录响应中获取所有 cookies
    cookies_list = login_result[1].cookies.items()
    # print("cookies_list:")
    # print(cookies_list)

    # 获取 AUTH_TOKEN
    auth_token = login_result[0].json()['data']
    # print("AUTH_TOKEN:")
    # print(auth_token)
    # 确保从正确的 session 中获取 BGWL-EXEC-PROD
    bgwl_exec_prod = login_result[1].cookies.get('BGWL-EXEC-PROD')
    cookies = {
        'IGNORE-SESSION': '-',
        'AUTH_TOKEN': auth_token,
        'BGWL-EXEC-PROD': bgwl_exec_prod,
        'HWWAFSESTIME': cookies_list[1][1],
        'HWWAFSESID': cookies_list[0][1],
    }
    print('最终的cookie:', cookies)


    return cookies



def login():
    """统一入口：获取 cookie，失败时返回 None。"""
    try:
        return build_login_cookies()
    except Exception as e:
        print(f"GWL-EXEC-PROD验证失败: {e}")
        return None


def save_api_data() -> Tuple[str, str, Optional[Path]]:
    """兼容旧命名：转调到 fetch_captcha。"""
    return fetch_captcha()


def get_cookie():
    """兼容旧命名：转调到 login_with_captcha。"""
    return login_with_captcha()


def verify_cookies():
    """兼容旧命名：转调到 build_login_cookies。"""
    return build_login_cookies()

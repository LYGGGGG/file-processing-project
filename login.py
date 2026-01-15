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
from dotenv import load_dotenv

# 模块级日志器：输出验证码与登录流程日志
logger = logging.getLogger(__name__)

def save_api_data() -> Tuple[str, str, Optional[Path]]:
    """请求验证码→识别→保存→返回 rs_id+code"""
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

    except Exception as e:
        logger.exception("获取验证码失败: %s", e)
        raise


"""生成一个算 MD5 的函数"""
def md5_hexdigest(text: str) -> str:
    """返回字符串的 MD5 十六进制摘要（用于登录密码）"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()

# 获取cookie
def get_cookie():
    """请求验证码并执行登录，返回可用 cookie 字典。"""
    rs_id, code, _ = save_api_data()
    logger.info("登录使用验证码: rs_id=%s code=%s", rs_id, code)

    load_dotenv()
    username = os.getenv("LOGIN_USERNAME", "").strip()
    password = os.getenv("LOGIN_PASSWORD", "").strip()

    # 登录 payload 使用 MD5 加密后的密码
    payload = {
        "rsid": rs_id,
        "code": code,
        "password": md5_hexdigest(password),
        "username": username,
    }

    payload = json.dumps(payload)
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json;charset=UTF-8",
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
    }
    session = requests.Session()
    # 1. 首先执行登录 POST 请求
    url = f"https://bgwlgl.bbwport.com/api/bgwl-cloud-center/login.do?_rs_id={rs_id}&_randomCode_={code}"
    login_response = session.post(url, headers=headers, data=payload)
    login_response.raise_for_status()

    # 从登录响应中获取所有 cookies
    cookies_list = list(session.cookies.items())

    # 获取 AUTH_TOKEN
    auth_token = login_response.json().get("data")
    if not auth_token:
        logger.error("登录响应内容: %s", login_response.text)
        raise RuntimeError("登录失败：未获取到 AUTH_TOKEN，请检查用户名/密码或验证码识别结果。")

    cookies = {
        'IGNORE-SESSION': '-',
        'AUTH_TOKEN': auth_token,
        'HWWAFSESTIME': cookies_list[1][1],
        'HWWAFSESID': cookies_list[0][1],
    }
    logger.info("最终 cookie: %s", cookies)
    return cookies

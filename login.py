# -*- coding: utf-8 -*-
import base64
import hashlib
import json
import re
from typing import Callable, Dict, Tuple

import ddddocr
import requests


def save_api_data() -> Tuple[str, str]:
    url = "https://bgwlgl.bbwport.com/api/bgwl-cloud-center/random?show=1753427607760"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    data = response.json()
    rs_id = data["_rs_id"]
    image_data = data["randomCodeImage"]
    match = re.match(r"data:image/(\\w+);base64,(.*)", image_data)
    if not match:
        raise ValueError("验证码图片格式解析失败")
    image_bytes = base64.b64decode(match.group(2))
    ocr = ddddocr.DdddOcr()
    text = ocr.classification(image_bytes)
    return rs_id, text


def create_output_method(method: str) -> Callable[[str], str]:
    def hash_func(input_str: str) -> str:
        md5 = hashlib.md5(input_str.encode("utf-8"))
        if method == "hexdigest":
            return md5.hexdigest()
        if method == "digest":
            return md5.digest().decode("latin1")
        raise ValueError(f"Unsupported method: {method}")

    return hash_func


def get_cookie() -> Tuple[Dict[str, str], requests.Session]:
    rs_id, code = save_api_data()
    md5_hex = create_output_method("hexdigest")
    data_list = {
        "rsid": rs_id,
        "code": code,
        "password": md5_hex("abc1234."),
        "username": "梁易高",
    }

    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json;charset=UTF-8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        ),
    }
    session = requests.Session()
    url = f"https://bgwlgl.bbwport.com/api/bgwl-cloud-center/login.do?_rs_id={rs_id}&_randomCode_={code}"
    login_response = session.post(url, headers=headers, data=json.dumps(data_list), timeout=15)
    login_response.raise_for_status()

    cookies_list = list(session.cookies.items())
    auth_token = login_response.json()["data"]
    bgwl_exec_prod = session.cookies.get("BGWL-EXEC-PROD")
    cookies = {
        "IGNORE-SESSION": "-",
        "AUTH_TOKEN": auth_token,
        "BGWL-EXEC-PROD": bgwl_exec_prod,
        "HWWAFSESTIME": cookies_list[1][1] if len(cookies_list) > 1 else "",
        "HWWAFSESID": cookies_list[0][1] if cookies_list else "",
    }

    return cookies, session


__all__ = ["get_cookie"]

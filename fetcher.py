import logging
import math
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from utils import deep_inject_env


# 模块级 logger：供本模块内部统一输出日志
logger = logging.getLogger(__name__)
# 统一日志格式，便于排查请求与分页等流程
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# -------------------------
# core: fetch listRealTrainInfo
# -------------------------

def fetch_all_real_train_info(
    url: str,
    headers: Dict[str, str],
    payload_template: Dict[str, Any],
    *,
    page_number_field: str = "pageNumber",
    page_size_field: str = "pageSize",
    rows_field: str = "rows",
    total_field: str = "total",
    start_page_number: int = 0,  # 你的接口是 0 起始
    retries: int = 3,
    timeout: int = 30,
    sleep_between_pages: float = 0.2,
    auth_link_flow: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    拉取 listRealTrainInfo.do 全量数据：
    - 分页字段：pageNumber/pageSize
    - 返回字段：total/rows

    参数:
        url: 列表接口 URL。
        headers: 请求头（可包含 ${ENV} 形式占位符）。
        payload_template: 请求 payload 模板（可包含 ${ENV} 形式占位符）。
        page_number_field: 分页页码字段名，默认 pageNumber。
        page_size_field: 分页大小字段名，默认 pageSize。
        rows_field: 返回数据中列表字段名，默认 rows。
        total_field: 返回数据中总数字段名，默认 total。
        start_page_number: 起始页码（接口若从 0 开始则填 0）。
        retries: 单次请求失败重试次数。
        timeout: 单次请求超时时间（秒）。
        sleep_between_pages: 每页请求之间休眠时间（秒），用于降低频率。

    返回:
        拼接所有分页后的 records 列表。
    """
    # 使用 Session 以复用连接，减少重复握手
    session = requests.Session()
    # 将 headers / payload 中的 ${ENV} 占位符替换为环境变量
    headers = deep_inject_env(headers)
    base_payload = deep_inject_env(payload_template)
    auth_link_flow = deep_inject_env(auth_link_flow or {})
    auth_link_state = {"used": False}

    def _parse_cookie_header(cookie_header: str) -> Dict[str, str]:
        cookies: Dict[str, str] = {}
        for part in cookie_header.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
        return cookies

    def _get_auth_token(cookie_header: str) -> Optional[str]:
        cookie_token = _parse_cookie_header(cookie_header).get("AUTH_TOKEN")
        header_token = headers.get("auth_token")
        env_token = os.getenv(auth_link_flow.get("auth_token_env", "AUTH_TOKEN"))
        return header_token or cookie_token or env_token

    def _apply_cookies_to_session() -> None:
        cookie_header = headers.get("cookie")
        if not cookie_header:
            return
        parsed = _parse_cookie_header(cookie_header)
        if parsed:
            session.cookies.update(parsed)

    def _run_auth_link_flow() -> None:
        if not auth_link_flow.get("enabled", False):
            return
        cookie_header = headers.get("cookie", "")
        auth_token = _get_auth_token(cookie_header)
        if not auth_token:
            logger.warning("auth-link 流程缺少 AUTH_TOKEN，跳过鉴权刷新。")
            return

        transfer_url = auth_link_flow.get(
            "transfer_url",
            "https://bgwlgl.bbwport.com/api/auth/transfer",
        )
        transfer_headers = auth_link_flow.get(
            "transfer_headers",
            {
                "accept": "application/json, text/plain, */*",
                "accept-language": "zh-CN,zh;q=0.9",
                "auth_token": auth_token,
                "content-type": "application/x-www-form-urlencoded",
                "origin": "https://bgwlgl.bbwport.com",
                "priority": "u=1, i",
                "referer": "https://bgwlgl.bbwport.com/",
                "user-agent": "Mozilla/5.0",
            },
        )
        transfer_headers = dict(transfer_headers)
        transfer_headers["auth_token"] = auth_token
        verify_ssl = auth_link_flow.get("verify_ssl", True)

        logger.info("auth-link transfer 请求 URL：%s", transfer_url)
        transfer_resp = session.post(
            transfer_url,
            headers=transfer_headers,
            timeout=timeout,
            verify=verify_ssl,
        )
        transfer_resp.raise_for_status()
        transfer_data = transfer_resp.json()
        auth_key_link = transfer_data.get("value")
        if not auth_key_link:
            logger.warning("auth-link transfer 未返回 value 字段，跳过后续刷新。")
            return

        portal_url = auth_link_flow.get("portal_url", "https://bgwlgl.bbwport.com:6443/")
        portal_headers = auth_link_flow.get(
            "portal_headers",
            {
                "Host": "bgwlgl.bbwport.com:6443",
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            },
        )
        portal_headers = dict(portal_headers)

        portal_request_url = f"{portal_url}?auth-key-link={auth_key_link}"
        logger.info("auth-link portal URL：%s", portal_request_url)
        session.get(
            portal_request_url,
            headers=portal_headers,
            timeout=timeout,
            verify=verify_ssl,
        )

        portal_headers["Referer"] = portal_request_url
        me_url = auth_link_flow.get("me_url", "https://bgwlgl.bbwport.com:6443/me.json")
        timestamp = int(time.time() * 1000)
        me_request_url = f"{me_url}?_d={timestamp}&auth-key-link={auth_key_link}"
        logger.info("auth-link me.json URL：%s", me_request_url)
        session.get(
            me_request_url,
            headers=portal_headers,
            timeout=timeout,
            verify=verify_ssl,
        )

    _apply_cookies_to_session()

    def _post_with_retry(data: Dict[str, Any]) -> Dict[str, Any]:
        """内部函数：负责带重试的 POST 请求。"""
        last_exc: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                logger.info("列表接口请求 URL：%s", url)
                resp = session.post(url, json=data, headers=headers, timeout=timeout)
                if (
                    resp.status_code == 401
                    and not auth_link_state["used"]
                    and auth_link_flow.get("enabled", False)
                ):
                    logger.warning("列表接口 401，尝试执行 auth-link 刷新流程。")
                    _run_auth_link_flow()
                    auth_link_state["used"] = True
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("请求失败（第 %s/%s 次）：%s", attempt, retries, exc)
                # 指数退避等待，降低短时间内连续失败的概率
                time.sleep(2 ** attempt)
        raise last_exc  # type: ignore[misc]

    # 先请求第一页，拿 total
    first_payload = dict(base_payload)
    # 起始页码写入 payload
    first_payload[page_number_field] = start_page_number

    first = _post_with_retry(first_payload)
    # total 为接口返回总数量；如果拿不到则按 0 处理
    total = int(first.get(total_field, 0) or 0)
    # page_size 取自 payload 模板
    page_size = int(base_payload.get(page_size_field, 200))
    rows = first.get(rows_field, []) or []
    all_rows: List[Dict[str, Any]] = list(rows)

    logger.info("分页汇总：总数=%s, 每页=%s, 首页条数=%s", total, page_size, len(rows))

    # 如果第一页已返回全部数据，直接返回
    if total <= len(all_rows):
        return all_rows

    # 计算总页数：total / page_size 向上取整
    total_pages = int(math.ceil(total / page_size))
    # total_pages 是“逻辑页数”，请求页码从 0 开始，所以循环 1..total_pages-1
    for i in range(1, total_pages):
        page_number = start_page_number + i
        payload = dict(base_payload)
        payload[page_number_field] = page_number

        data = _post_with_retry(payload)
        rows = data.get(rows_field, []) or []
        all_rows.extend(rows)

        logger.info(
            "分页拉取：第 %s/%s 页，获取=%s，累计=%s",
            i + 1,
            total_pages,
            len(rows),
            len(all_rows),
        )
        # 控制请求频率，避免被接口限流
        time.sleep(sleep_between_pages)

    return all_rows


# -------------------------
# filter: pick codes for a day
# -------------------------

def filter_codes_for_day(rows: List[Dict[str, Any]], day: str) -> List[str]:
    """
    从 listRealTrainInfo 的 rows 中筛选指定日期(day='YYYY-MM-DD')的 real_train_code。
    规则：departure_date 的日期部分 == day

    参数:
        rows: 列表接口返回的记录列表。
        day: 目标日期，格式为 YYYY-MM-DD。

    返回:
        去重后的 real_train_code 列表（保持原顺序）。
    """
    codes: List[str] = []

    for r in rows:
        # departure_date 字段可能包含时间，先取日期部分
        dep = (r.get("departure_date") or "").strip()
        if len(dep) >= 10 and dep[:10] == day:
            code = (r.get("real_train_code") or "").strip()
            if code:
                codes.append(code)

    # 去重保持顺序
    dedup: List[str] = []
    seen = set()
    for c in codes:
        if c not in seen:
            seen.add(c)
            dedup.append(c)

    return dedup


# download: exportLoadedBox.do (xlsx binary)
# -------------------------

def download_export_loaded_box_xlsx(
    *,
    url: str,
    headers: Dict[str, str],
    real_train_codes: List[str],
    out_path: str,
    flag: str = "单表",
    retries: int = 3,
    timeout: int = 60,
) -> str:
    """
    调用 exportLoadedBox.do 下载 Excel（二进制）并保存。
    payload: {"realTrainCode": "A,B,C", "flag":"单表"}

    参数:
        url: 导出接口 URL。
        headers: 请求头（可包含 ${ENV} 形式占位符）。
        real_train_codes: 需要导出的 real_train_code 列表。
        out_path: 本地保存路径（含文件名）。
        flag: 接口需要的导出标识（默认“单表”）。
        retries: 下载失败时的重试次数。
        timeout: 单次下载超时时间（秒）。

    返回:
        保存后的文件路径字符串。
    """
    if not real_train_codes:
        raise ValueError("real_train_codes 为空，无法导出")

    # Session 复用连接
    session = requests.Session()
    # 注入环境变量（避免把 token 写死在代码里）
    headers = deep_inject_env(headers)

    # 逗号拼接 codes，符合接口参数要求
    payload = {"realTrainCode": ",".join(real_train_codes), "flag": flag}

    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            logger.info("导出接口请求 URL：%s", url)
            resp = session.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()

            # 确保目录存在
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)

            # 保存为 xlsx
            with open(out_path, "wb") as f:
                f.write(resp.content)

            return out_path
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("导出下载失败（第 %s/%s 次）：%s", attempt, retries, exc)
            # 下载失败时指数退避等待
            time.sleep(2 ** attempt)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("导出下载失败，但未捕获到异常")

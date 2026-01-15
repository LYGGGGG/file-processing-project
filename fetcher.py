import logging
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from utils import deep_inject_env, normalize_auth_headers, parse_cookie_header


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _prepare_session(
    headers: Dict[str, str],
    session: Optional[requests.Session] = None,
) -> Tuple[requests.Session, Dict[str, str]]:
    """准备 Session 并同步鉴权与 cookie。"""
    session = session or requests.Session()
    headers = deep_inject_env(headers)
    normalize_auth_headers(headers)

    cookie_header = headers.get("cookie")
    if cookie_header:
        cookies = parse_cookie_header(cookie_header)
        if cookies:
            session.cookies.update(cookies)

    return session, headers


def _request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    headers: Dict[str, str],
    payload: Optional[Dict[str, Any]] = None,
    *,
    retries: int,
    retry_backoff_base: float,
    timeout: int,
    log_label: str,
    expect_json: bool = True,
) -> Any:
    """带重试的请求。"""
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            logger.info("%s 请求 URL：%s", log_label, url)
            resp = session.request(
                method,
                url,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json() if expect_json else resp.content
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("%s 请求失败（第 %s/%s 次）：%s", log_label, attempt, retries, exc)
            time.sleep(retry_backoff_base**attempt)

    raise last_exc  # type: ignore[misc]


def fetch_train_rows(
    url: str,
    headers: Dict[str, str],
    payload_template: Dict[str, Any],
    *,
    session: Optional[requests.Session] = None,
    page_number_field: str = "pageNumber",
    page_size_field: str = "pageSize",
    rows_field: str = "rows",
    total_field: str = "total",
    start_page_number: int = 0,
    retries: int = 3,
    retry_backoff_base: float = 2.0,
    timeout: int = 30,
    sleep_between_pages: float = 0.2,
) -> List[Dict[str, Any]]:
    """分页拉取 listRealTrainInfo.do 返回的 rows。"""
    session, headers = _prepare_session(headers, session)
    base_payload = deep_inject_env(payload_template)

    first_payload = dict(base_payload)
    first_payload[page_number_field] = start_page_number

    def post(payload: Dict[str, Any]) -> Dict[str, Any]:
        return _request_with_retry(
            session,
            "POST",
            url,
            headers,
            payload,
            retries=retries,
            retry_backoff_base=retry_backoff_base,
            timeout=timeout,
            log_label="列表接口",
            expect_json=True,
        )

    first = post(first_payload)
    total = int(first.get(total_field, 0) or 0)
    page_size = int(base_payload.get(page_size_field, 200))
    rows = first.get(rows_field, []) or []
    all_rows: List[Dict[str, Any]] = list(rows)

    logger.info("分页汇总：总数=%s, 每页=%s, 首页条数=%s", total, page_size, len(rows))
    if total <= len(all_rows):
        return all_rows

    total_pages = int(math.ceil(total / page_size))
    for i in range(1, total_pages):
        payload = dict(base_payload)
        payload[page_number_field] = start_page_number + i
        data = post(payload)
        rows = data.get(rows_field, []) or []
        all_rows.extend(rows)

        logger.info(
            "分页拉取：第 %s/%s 页，获取=%s，累计=%s",
            i + 1,
            total_pages,
            len(rows),
            len(all_rows),
        )
        time.sleep(sleep_between_pages)

    return all_rows


def _parse_departure_datetime(value: str) -> Optional[datetime]:
    """解析出发时间字符串。"""
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def filter_train_codes_by_day(
    rows: List[Dict[str, Any]],
    day: str,
    *,
    departureDateStart: Optional[str] = None,
    departureDateEnd: Optional[str] = None,
) -> List[str]:
    """在本地按时间范围筛选 real_train_code。"""
    start_dt = _parse_departure_datetime((departureDateStart or "").strip())
    end_dt = _parse_departure_datetime((departureDateEnd or "").strip())
    codes: List[str] = []

    for row in rows:
        dep = (row.get("departure_date") or "").strip()
        dep_dt = _parse_departure_datetime(dep)
        if start_dt and end_dt and dep_dt:
            if start_dt <= dep_dt <= end_dt:
                code = (row.get("real_train_code") or "").strip()
                if code:
                    codes.append(code)
            continue
        if len(dep) >= 10 and dep[:10] == day:
            code = (row.get("real_train_code") or "").strip()
            if code:
                codes.append(code)

    return list(dict.fromkeys(codes))


def download_export_excel(
    *,
    url: str,
    headers: Dict[str, str],
    real_train_codes: List[str],
    out_path: str,
    flag: str = "单表",
    session: Optional[requests.Session] = None,
    retries: int = 3,
    retry_backoff_base: float = 2.0,
    timeout: int = 60,
) -> str:
    """调用 exportLoadedBox.do 下载 Excel。"""
    if not real_train_codes:
        raise ValueError("real_train_codes 为空，无法导出")

    session, headers = _prepare_session(headers, session)
    payload = {"realTrainCode": ",".join(real_train_codes), "flag": flag}

    content = _request_with_retry(
        session,
        "POST",
        url,
        headers,
        payload,
        retries=retries,
        retry_backoff_base=retry_backoff_base,
        timeout=timeout,
        log_label="导出接口",
        expect_json=False,
    )

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as file_obj:
        file_obj.write(content)

    return out_path

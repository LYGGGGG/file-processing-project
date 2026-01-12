# fetcher.py
import os
import time
import math
import json
import logging
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

load_dotenv()  # 读取 .env


# -------------------------
# helpers: env inject
# -------------------------
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
) -> List[Dict[str, Any]]:
    """
    拉取 listRealTrainInfo.do 全量数据：
    - 分页字段：pageNumber/pageSize
    - 返回字段：total/rows
    """
    session = requests.Session()
    headers = _deep_inject_env(headers)
    base_payload = _deep_inject_env(payload_template)

    def _post_with_retry(data: Dict[str, Any]) -> Dict[str, Any]:
        last_exc: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                resp = session.post(url, json=data, headers=headers, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("Request failed (attempt %s/%s): %s", attempt, retries, exc)
                time.sleep(2 ** attempt)
        raise last_exc  # type: ignore[misc]

    # 先请求第一页，拿 total
    first_payload = dict(base_payload)
    first_payload[page_number_field] = start_page_number

    first = _post_with_retry(first_payload)
    total = int(first.get(total_field, 0) or 0)
    page_size = int(base_payload.get(page_size_field, 200))
    rows = first.get(rows_field, []) or []
    all_rows: List[Dict[str, Any]] = list(rows)

    logger.info("total=%s, page_size=%s, first_page_rows=%s", total, page_size, len(rows))

    if total <= len(all_rows):
        return all_rows

    total_pages = int(math.ceil(total / page_size))
    # total_pages 是“逻辑页数”，请求页码从 0 开始，所以循环 1..total_pages-1
    for i in range(1, total_pages):
        page_number = start_page_number + i
        payload = dict(base_payload)
        payload[page_number_field] = page_number

        data = _post_with_retry(payload)
        rows = data.get(rows_field, []) or []
        all_rows.extend(rows)

        logger.info("page %s/%s -> fetched=%s, accumulated=%s", i + 1, total_pages, len(rows), len(all_rows))
        time.sleep(sleep_between_pages)

    return all_rows


# -------------------------
# filter: pick codes for a day
# -------------------------
def filter_codes_for_day(rows: List[Dict[str, Any]], day: str) -> List[str]:
    """
    从 listRealTrainInfo 的 rows 中筛选指定日期(day='YYYY-MM-DD')的 real_train_code。
    规则：departure_date 的日期部分 == day
    """
    codes: List[str] = []

    for r in rows:
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


# -------------------------
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
    """
    if not real_train_codes:
        raise ValueError("real_train_codes is empty")

    session = requests.Session()
    headers = _deep_inject_env(headers)

    payload = {"realTrainCode": ",".join(real_train_codes), "flag": flag}

    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
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
            logger.warning("Export download failed (attempt %s/%s): %s", attempt, retries, exc)
            time.sleep(2 ** attempt)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Export download failed but no exception captured")


# -------------------------
# main
# -------------------------
def main() -> None:
    LIST_URL = "https://bgwlgl.bbwport.com/api/train-sea-union/real/train/listRealTrainInfo.do"
    EXPORT_URL = "https://bgwlgl.bbwport.com/api/train-sea-union/bookingInfo/exportLoadedBox.do"

    HEADERS = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json;charset=UTF-8",
        "origin": "https://bgwlgl.bbwport.com",
        "referer": "https://bgwlgl.bbwport.com/",
        "auth_token": "${AUTH_TOKEN}",
        "cookie": "${COOKIE}",
        "user-agent": "Mozilla/5.0",
    }

    PAYLOAD = {
        "pageNumber": 0,
        "pageSize": 200,
        "params": {
            "realTrainCode": "",
            "startStation": "",
            "endStation": "",
            "lineCode": "",
            "lineName": "",
            "loadingTimeStart": "",
            "loadingTimeEnd": "",
            # 你也可以不依赖接口时间过滤（接口会混入未来数据），反正我们本地再过滤
            "departureDateStart": "2026-01-12 08:25:37",
            "departureDateEnd": None,
            "endProvince": "",
            "upOrDown": "",
        },
        "sorts": [],
    }

    # 1) 拉列表
    rows = fetch_all_real_train_info(LIST_URL, HEADERS, PAYLOAD)
    logger.info("listRealTrainInfo rows=%s", len(rows))

    # 保存一份原始列表便于你核对
    with open("sample_rows.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    logger.info("saved sample_rows.json")

    # 2) 本地筛“当天” code（先写死，后续你改成自动）
    today = "2026-01-12"
    codes = filter_codes_for_day(rows, today)
    logger.info("filtered codes for %s => %s", today, len(codes))
    logger.info("codes=%s", ",".join(codes))

    # 3) 下载导出 Excel
    out_file = f"data/export_loaded_box_{today}.xlsx"
    saved = download_export_loaded_box_xlsx(
        url=EXPORT_URL,
        headers=HEADERS,
        real_train_codes=codes,
        out_path=out_file,
        flag="单表",
    )
    logger.info("saved excel => %s", saved)


if __name__ == "__main__":
    main()

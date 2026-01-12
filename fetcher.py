import logging
import time
from typing import Any, Dict, List

import requests


logger = logging.getLogger(__name__)


def _get_by_path(data: Dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def fetch_all_pages(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    api_cfg = config["api"]
    pagination = api_cfg["pagination"]
    url = api_cfg["url"]
    method = api_cfg.get("method", "POST").upper()
    headers = api_cfg.get("headers", {})
    payload = api_cfg.get("payload", {}).copy()

    session = requests.Session()
    retries = api_cfg.get("retries", 3)
    timeout = api_cfg.get("timeout", 30)
    start_page = pagination.get("start_page", 1)
    max_pages = pagination.get("max_pages", 1000)

    all_records: List[Dict[str, Any]] = []

    for page in range(start_page, start_page + max_pages):
        payload[pagination["page_param"]] = page
        payload[pagination["page_size_param"]] = pagination["page_size"]

        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                logger.info("Fetching page %s (attempt %s)", page, attempt)
                if method == "POST":
                    response = session.post(url, json=payload, headers=headers, timeout=timeout)
                else:
                    response = session.get(url, params=payload, headers=headers, timeout=timeout)
                response.raise_for_status()
                data = response.json()
                records = _get_by_path(data, pagination["list_field"]) or []
                all_records.extend(records)

                total_pages = _get_by_path(data, pagination.get("total_pages_field", ""))
                if total_pages and page >= int(total_pages):
                    return all_records
                if len(records) < pagination["page_size"]:
                    return all_records
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("Page %s failed: %s", page, exc)
                time.sleep(2**attempt)
        if last_exc:
            raise last_exc
    return all_records

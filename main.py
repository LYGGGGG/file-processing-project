import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

import login
from fetcher import (
    download_export_loaded_box_xlsx,
    fetch_all_real_train_info,
    filter_codes_for_day,
)

from processor import split_excel_by_actual_booker
from utils import build_cookie_header

from config import CONFIG

# 主入口日志
logger = logging.getLogger(__name__)
# 统一日志格式，便于排查流程
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")



def _missing_env_vars(headers: Dict[str, Any]) -> Dict[str, str]:
    missing: Dict[str, str] = {}
    for key, value in headers.items():
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_key = value[2:-1]
            if not os.getenv(env_key):
                missing[key] = env_key
    return missing


def _validate_auth_headers(section: str, headers: Dict[str, Any]) -> None:
    auth_keys = [key for key in ("auth_token", "cookie") if key in headers]
    if not auth_keys:
        return

    missing_envs = _missing_env_vars(headers)
    empty_auth = [key for key in auth_keys if key in missing_envs]
    if empty_auth and len(empty_auth) == len(auth_keys):
        missing_vars = ", ".join(sorted({missing_envs[key] for key in empty_auth}))
        raise RuntimeError(
            f"{section} 鉴权信息为空，无法访问接口。请设置环境变量 {missing_vars}，"
            "或启用 login_api 自动登录后重试。"
        )


def _sync_auth_token_from_cookie(cookie_header: str, env_key: str = "AUTH_TOKEN") -> None:
    if not cookie_header or os.getenv(env_key):
        return
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip() == "AUTH_TOKEN" and value.strip():
            os.environ[env_key] = value.strip()
            return


def _resolve_target_day(run_cfg: Dict[str, Any]) -> str:
    target_day = run_cfg.get("target_day", "")
    if not target_day:
        target_day = date.today().strftime("%Y-%m-%d")
        run_cfg["target_day"] = target_day
    return target_day


def _sync_departure_date(list_cfg: Dict[str, Any], target_day: str) -> None:
    payload = list_cfg.get("payload_template", {})
    params = payload.get("params", {})
    if not params.get("departureDateStart"):
        params["departureDateStart"] = f"{target_day} 00:00:00"
    params.pop("departureDateEnd", None)
    payload["params"] = params
    list_cfg["payload_template"] = payload


def main() -> None:
    """主流程入口：读取配置 -> 拉取列表 -> 筛选 -> 下载 Excel。"""
    # 读取 .env（如果存在），将 AUTH_TOKEN / COOKIE 等注入环境变量
    load_dotenv()
    config = CONFIG
    login_cfg = config.get("login_api", {})
    if login_cfg.get("enabled", False):
        cookies = login.login()
        if not cookies:
            raise RuntimeError("登录失败，未获取到 Cookie 信息。")
        cookie_header = build_cookie_header(
            cookie_pairs=cookies,
            preferred_keys=("AUTH_TOKEN", "BGWL-EXEC-PROD", "HWWAFSESID", "HWWAFSESTIME"),
        )
        os.environ[login_cfg.get("cookie_env", "COOKIE")] = cookie_header
        _sync_auth_token_from_cookie(cookie_header, env_key=login_cfg.get("token_env", "AUTH_TOKEN"))
    else:
        cookie_env_key = login_cfg.get("cookie_env", "COOKIE")
        _sync_auth_token_from_cookie(os.getenv(cookie_env_key, ""))

    _validate_auth_headers("list_api", config["list_api"].get("headers", {}))
    _validate_auth_headers("export_api", config["export_api"].get("headers", {}))

    # 列表接口配置
    list_cfg = config["list_api"]
    # 分页相关配置（可选）
    pagination_cfg = list_cfg.get("pagination", {})
    # 运行期参数（如目标日期、输出路径等）
    run_cfg = config["run"]
    target_day = _resolve_target_day(run_cfg)
    _sync_departure_date(list_cfg, target_day)

    # 1) 拉取列表数据（分页合并后的所有 rows）
    rows = fetch_all_real_train_info(
        list_cfg["url"],
        list_cfg["headers"],
        list_cfg["payload_template"],
        page_number_field=pagination_cfg.get("page_param", "pageNumber"),
        page_size_field=pagination_cfg.get("page_size_param", "pageSize"),
        rows_field=pagination_cfg.get("rows_field", "rows"),
        total_field=pagination_cfg.get("total_field", "total"),
        start_page_number=pagination_cfg.get("start_page", 0),
        retries=list_cfg.get("retries", 3),
        timeout=list_cfg.get("timeout", 30),
        sleep_between_pages=list_cfg.get("sleep_between_pages", 0.2),
        auth_link_flow=list_cfg.get("auth_link_flow"),
    )
    logger.info("列表接口返回条数=%s", len(rows))

    # 2) 可选保存原始 rows，便于核对/调试
    if run_cfg.get("save_sample_rows", False):
        sample_path = run_cfg.get("sample_rows_path", "sample_rows.json")
        with open(sample_path, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=2)
        logger.info("已保存 sample_rows.json -> %s", sample_path)

    # 3) 本地按日期筛选 real_train_code
    codes = filter_codes_for_day(rows, target_day)
    logger.info("按日期 %s 筛选车次数量=%s", target_day, len(codes))
    logger.info("车次清单=%s", ",".join(codes))

    # 4) 根据配置拼接输出文件路径
    export_cfg = config["export_api"]
    output_dir = Path(run_cfg.get("output_dir", "data"))
    output_template = run_cfg.get("output_filename_template", "export_loaded_box_{day}.xlsx")
    out_file = output_dir / output_template.format(day=target_day)

    # 5) 下载 Excel 并保存到本地
    saved = download_export_loaded_box_xlsx(
        url=export_cfg["url"],
        headers=export_cfg["headers"],
        real_train_codes=codes,
        out_path=str(out_file),
        flag=export_cfg.get("flag", "单表"),
        retries=export_cfg.get("retries", 3),
        timeout=export_cfg.get("timeout", 60),
    )
    logger.info("已保存 Excel => %s", saved)

    # 6) 对下载的 Excel 进一步处理：按委托客户过滤并按实际订舱客户拆分

    processing_cfg = config.get("processing", {})
    if processing_cfg.get("enabled", True):
        consigner_env_key = processing_cfg.get("consigner_env_key", "")
        consigner_value = os.getenv(consigner_env_key, "") if consigner_env_key else ""
        outputs = split_excel_by_actual_booker(
            input_path=saved,
            output_dir=processing_cfg.get("output_dir", "data/actual_booker"),
            consigner_field=processing_cfg.get("consigner_field", "委托客户"),
            consigner_value=consigner_value,
            actual_booker_field=processing_cfg.get("actual_booker_field", "实际订舱客户"),
            actual_booker_exclude=processing_cfg.get("actual_booker_exclude"),
            sheet_name=processing_cfg.get("sheet_name", "data"),
            output_template=processing_cfg.get("output_template", "{actual_booker}.xlsx"),
        )
        logger.info("实际订舱客户拆分输出=%s", list(outputs.values()))


if __name__ == "__main__":
    main()

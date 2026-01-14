import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

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

# 主入口日志：统一使用模块名，便于在多模块日志中定位来源
logger = logging.getLogger(__name__)
# 统一日志格式，便于排查流程与线上问题
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")



def _missing_env_vars(headers: Dict[str, Any]) -> Dict[str, str]:
    """扫描 headers 中形如 ${ENV} 的占位符，返回缺失环境变量映射。"""
    missing: Dict[str, str] = {}
    for key, value in headers.items():
        # 仅处理字符串占位符，其它类型保持原样
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_key = value[2:-1]
            # 未找到环境变量时记录该 key
            if not os.getenv(env_key):
                missing[key] = env_key
    return missing


def _validate_auth_headers(section: str, headers: Dict[str, Any]) -> None:
    """校验鉴权配置，避免 auth_token/cookie 都为空时继续请求接口。"""
    # 只在 headers 中存在鉴权相关 key 时才进行校验
    auth_keys = [key for key in ("auth_token", "cookie") if key in headers]
    if not auth_keys:
        return

    missing_envs = _missing_env_vars(headers)
    # 如果 auth_token/cookie 都是空占位符，则明确报错提示
    empty_auth = [key for key in auth_keys if key in missing_envs]
    if empty_auth and len(empty_auth) == len(auth_keys):
        missing_vars = ", ".join(sorted({missing_envs[key] for key in empty_auth}))
        raise RuntimeError(
            f"{section} 鉴权信息为空，无法访问接口。请设置环境变量 {missing_vars}，"
            "或启用 login_api 自动登录后重试。"
        )


def _sync_auth_token_from_cookie(cookie_header: str, env_key: str = "AUTH_TOKEN") -> None:
    """当环境变量缺失 AUTH_TOKEN 时，尝试从 cookie 中同步写入。"""
    # 若 cookie 不存在或环境变量已存在，则无需处理
    if not cookie_header or os.getenv(env_key):
        return
    # 解析 cookie，寻找 AUTH_TOKEN
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip() == "AUTH_TOKEN" and value.strip():
            os.environ[env_key] = value.strip()
            return


def _resolve_target_day(run_cfg: Dict[str, Any]) -> str:
    """读取或生成目标日期，并写回 run_cfg 以便后续流程复用。"""
    target_day = run_cfg.get("target_day", "")
    if not target_day:
        # 默认取当天，格式与接口要求一致
        target_day = date.today().strftime("%Y-%m-%d")
        run_cfg["target_day"] = target_day
    return target_day


def _sync_departure_date(list_cfg: Dict[str, Any], target_day: str) -> None:
    """将目标日期同步到列表请求 payload，确保仅查询当天数据。"""
    payload = list_cfg.get("payload_template", {})
    params = payload.get("params", {})
    # 如果未设置出发日期，则补齐当天 00:00:00
    if not params.get("departureDateStart"):
        params["departureDateStart"] = f"{target_day} 00:00:00"
    # 避免配置中残留结束日期造成筛选冲突
    params.pop("departureDateEnd", None)
    payload["params"] = params
    list_cfg["payload_template"] = payload


def _prepare_auth(config: Dict[str, Any]) -> None:
    """准备鉴权信息：加载 .env、登录（可选）、同步 token 并校验配置。"""
    # 1) 读取 .env（如果存在），将 AUTH_TOKEN / COOKIE 等注入环境变量
    load_dotenv()
    login_config = config.get("login_api", {})
    if login_config.get("enabled", False):
        # 2) 自动登录获取 cookie（包含 AUTH_TOKEN）
        cookies = login.login()
        if not cookies:
            raise RuntimeError("登录失败，未获取到 Cookie 信息。")
        cookie_header = build_cookie_header(
            cookie_pairs=cookies,
            preferred_keys=("AUTH_TOKEN", "BGWL-EXEC-PROD", "HWWAFSESID", "HWWAFSESTIME"),
        )
        os.environ[login_config.get("cookie_env", "COOKIE")] = cookie_header
        _sync_auth_token_from_cookie(cookie_header, env_key=login_config.get("token_env", "AUTH_TOKEN"))
    else:
        # 2) 不登录时，仅使用已有 cookie，并尝试补齐 AUTH_TOKEN
        cookie_env_key = login_config.get("cookie_env", "COOKIE")
        _sync_auth_token_from_cookie(os.getenv(cookie_env_key, ""))

    # 3) 在进入请求前确认鉴权信息齐全
    _validate_auth_headers("list_api", config["list_api"].get("headers", {}))
    _validate_auth_headers("export_api", config["export_api"].get("headers", {}))


def _fetch_rows(list_api: Dict[str, Any], pagination: Dict[str, Any]) -> List[Dict[str, Any]]:
    """执行列表查询并返回合并后的 rows。"""
    # 1) 拉取列表数据（分页合并后的所有 rows）
    return fetch_all_real_train_info(
        list_api["url"],
        list_api["headers"],
        list_api["payload_template"],
        page_number_field=pagination.get("page_param", "pageNumber"),
        page_size_field=pagination.get("page_size_param", "pageSize"),
        rows_field=pagination.get("rows_field", "rows"),
        total_field=pagination.get("total_field", "total"),
        start_page_number=pagination.get("start_page", 0),
        retries=list_api.get("retries", 3),
        timeout=list_api.get("timeout", 30),
        sleep_between_pages=list_api.get("sleep_between_pages", 0.2),
        auth_link_flow=list_api.get("auth_link_flow"),
    )

def _export_excel(export_api: Dict[str, Any], out_path: str, train_codes: List[str]) -> str:
    """调用导出接口下载 Excel 并返回本地文件路径。"""
    # 1) 下载 Excel 并保存到本地
    saved = download_export_loaded_box_xlsx(
        url=export_api["url"],
        headers=export_api["headers"],
        real_train_codes=train_codes,
        out_path=out_path,
        flag=export_api.get("flag", "单表"),
        retries=export_api.get("retries", 3),
        timeout=export_api.get("timeout", 60),
    )
    return saved


def _post_process_excel(processing: Dict[str, Any], input_path: str) -> None:
    """按配置拆分 Excel（委托客户过滤 + 实际订舱客户拆分）。"""
    if not processing.get("enabled", True):
        return
    # 1) 从环境变量读取委托客户名称，便于外部配置
    consigner_env_key = processing.get("consigner_env_key", "")
    consigner_value = os.getenv(consigner_env_key, "") if consigner_env_key else ""
    # 2) 执行拆分
    outputs = split_excel_by_actual_booker(
        input_path=input_path,
        output_dir=processing.get("output_dir", "data/actual_booker"),
        consigner_field=processing.get("consigner_field", "委托客户"),
        consigner_value=consigner_value,
        actual_booker_field=processing.get("actual_booker_field", "实际订舱客户"),
        actual_booker_exclude=processing.get("actual_booker_exclude"),
        sheet_name=processing.get("sheet_name", "data"),
        output_template=processing.get("output_template", "{actual_booker}.xlsx"),
    )
    logger.info("实际订舱客户拆分输出=%s", list(outputs.values()))


def main() -> None:
    """主流程入口：读取配置 -> 拉取列表 -> 筛选 -> 下载 Excel。"""
    # 0) 初始化配置
    config = CONFIG

    # 1) 准备鉴权信息（登录/同步 token/校验）
    _prepare_auth(config)

    # 2) 读取运行期参数并补齐目标日期
    run_config = config["run"]
    target_day = _resolve_target_day(run_config)

    # 3) 准备列表接口配置并同步查询日期
    list_api = config["list_api"]
    _sync_departure_date(list_api, target_day)
    pagination = list_api.get("pagination", {})

    # 4) 拉取列表数据
    rows = _fetch_rows(list_api, pagination)
    logger.info("列表接口返回条数=%s", len(rows))

    # 5) 可选保存原始 rows，便于核对/调试
    if run_config.get("save_sample_rows", False):
        sample_path = run_config.get("sample_rows_path", "sample_rows.json")
        with open(sample_path, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=2)
        logger.info("已保存 sample_rows.json -> %s", sample_path)

    # 6) 本地按日期筛选 real_train_code
    train_codes = filter_codes_for_day(rows, target_day)
    logger.info("按日期 %s 筛选车次数量=%s", target_day, len(train_codes))
    logger.info("车次清单=%s", ",".join(train_codes))

    # 7) 导出 Excel（先拼接输出路径）
    export_api = config["export_api"]
    output_dir = Path(run_config.get("output_dir", "data"))
    output_template = run_config.get("output_filename_template", "export_loaded_box_{day}.xlsx")
    out_path = output_dir / output_template.format(day=target_day)
    saved_path = _export_excel(export_api, str(out_path), train_codes)
    logger.info("已保存 Excel => %s", saved_path)

    # 8) 对下载的 Excel 进一步处理：按委托客户过滤并按实际订舱客户拆分
    processing = config.get("processing", {})
    _post_process_excel(processing, saved_path)


if __name__ == "__main__":
    main()

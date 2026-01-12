import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv

from fetcher import (
    download_export_loaded_box_xlsx,
    fetch_all_real_train_info,
    filter_codes_for_day,
)

from processor import split_excel_by_province

# 主入口日志
logger = logging.getLogger(__name__)
# 统一日志格式，便于排查流程
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def load_config(path: str) -> Dict[str, Any]:
    """读取 YAML 配置文件并解析为 dict。

    参数:
        path: 配置文件路径（如 config.yaml）。

    返回:
        YAML 解析后的字典对象。
    """
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def main() -> None:
    """主流程入口：读取配置 -> 拉取列表 -> 筛选 -> 下载 Excel。"""
    # 读取 .env（如果存在），将 AUTH_TOKEN / COOKIE 等注入环境变量
    load_dotenv()
    # 读取配置文件
    config = load_config("config.yaml")

    # 列表接口配置
    list_cfg = config["list_api"]
    # 分页相关配置（可选）
    pagination_cfg = list_cfg.get("pagination", {})
    # 运行期参数（如目标日期、输出路径等）
    run_cfg = config["run"]

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
    )
    logger.info("listRealTrainInfo rows=%s", len(rows))

    # 2) 可选保存原始 rows，便于核对/调试
    if run_cfg.get("save_sample_rows", False):
        sample_path = run_cfg.get("sample_rows_path", "sample_rows.json")
        with open(sample_path, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=2)
        logger.info("saved sample_rows.json -> %s", sample_path)

    # 3) 本地按日期筛选 real_train_code
    target_day = run_cfg["target_day"]
    codes = filter_codes_for_day(rows, target_day)
    logger.info("filtered codes for %s => %s", target_day, len(codes))
    logger.info("codes=%s", ",".join(codes))

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
    logger.info("saved excel => %s", saved)

    # 6) 对下载的 Excel 进一步处理：替换“委托客户”并按省份拆分
    processing_cfg = config.get("processing", {})
    if processing_cfg.get("enabled", True):
        consigner_env_key = processing_cfg.get("consigner_env_key", "")
        consigner_value = os.getenv(consigner_env_key, "") if consigner_env_key else ""

        outputs = split_excel_by_province(
            input_path=saved,
            output_dir=processing_cfg.get("output_dir", "data/province"),
            province_field=processing_cfg.get("province_field", "省份"),
            consigner_field=processing_cfg.get("consigner_field", "委托客户"),
            consigner_value=consigner_value,
            sheet_name=processing_cfg.get("sheet_name", "data"),
            output_template=processing_cfg.get("output_template", "{province}.xlsx"),
        )
        logger.info("province split outputs=%s", list(outputs.values()))


if __name__ == "__main__":
    main()

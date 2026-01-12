import json
import logging
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv

from fetcher import (
    download_export_loaded_box_xlsx,
    fetch_all_real_train_info,
    filter_codes_for_day,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def main() -> None:
    load_dotenv()
    config = load_config("config.yaml")

    list_cfg = config["list_api"]
    pagination_cfg = list_cfg.get("pagination", {})
    run_cfg = config["run"]

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

    if run_cfg.get("save_sample_rows", False):
        sample_path = run_cfg.get("sample_rows_path", "sample_rows.json")
        with open(sample_path, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=2)
        logger.info("saved sample_rows.json -> %s", sample_path)

    target_day = run_cfg["target_day"]
    codes = filter_codes_for_day(rows, target_day)
    logger.info("filtered codes for %s => %s", target_day, len(codes))
    logger.info("codes=%s", ",".join(codes))

    export_cfg = config["export_api"]
    output_dir = Path(run_cfg.get("output_dir", "data"))
    output_template = run_cfg.get("output_filename_template", "export_loaded_box_{day}.xlsx")
    out_file = output_dir / output_template.format(day=target_day)

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


if __name__ == "__main__":
    main()

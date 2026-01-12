import logging
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


logger = logging.getLogger(__name__)


def export_by_direction(grouped: Dict[str, List[Dict[str, Any]]], config: Dict[str, Any]) -> Dict[str, Path]:
    output_cfg = config["output"]
    output_dir = Path(output_cfg["dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    template = output_cfg["file_template"]
    sheet_name = output_cfg.get("sheet_name", "data")
    dedup_keys = config["dedup_keys"]

    outputs: Dict[str, Path] = {}

    for direction, records in grouped.items():
        if not records:
            continue
        file_path = output_dir / template.format(direction=direction)
        new_df = pd.DataFrame(records)

        if file_path.exists():
            existing_df = pd.read_excel(file_path, sheet_name=sheet_name)
            combined = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            combined = new_df

        combined = combined.drop_duplicates(subset=dedup_keys)
        combined.to_excel(file_path, index=False, sheet_name=sheet_name, engine="openpyxl")
        outputs[direction] = file_path
        logger.info("Exported %s records to %s", len(combined), file_path)

    return outputs

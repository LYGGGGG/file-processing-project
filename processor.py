"""处理下载的 Excel：按“委托客户”过滤并按“实际订舱客户”拆分成子表。"""


from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _sanitize_filename(name: str) -> str:
    """将省份名转换为安全文件名，避免路径或特殊字符导致保存失败。"""
    return (
        name.replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace("*", "_")
        .replace("?", "_")
        .replace("\"", "_")
        .replace("<", "_")
        .replace(">", "_")
        .replace("|", "_")
        .strip()
    )


def split_excel_by_actual_booker(
    *,
    input_path: str,
    output_dir: str,
    consigner_field: str = "委托客户",
    consigner_value: Optional[str] = None,
    actual_booker_field: str = "实际订舱客户",
    actual_booker_exclude: Optional[str] = None,
    sheet_name: str = "data",
    output_template: str = "{actual_booker}.xlsx",
) -> Dict[str, Path]:
    """读取 Excel，按“委托客户”过滤，并按“实际订舱客户”拆分为多个文件。

    参数:
        input_path: 下载后的 Excel 路径。
        output_dir: 拆分后的文件输出目录。
        consigner_field: 委托客户字段名。
        consigner_value: 委托客户过滤值（来自环境变量）；为空则不做过滤。
        actual_booker_field: 实际订舱客户字段名。
        actual_booker_exclude: 实际订舱客户需过滤掉的值；为空则不过滤。
        sheet_name: 输出 Excel 的 sheet 名。
        output_template: 输出文件名模板，支持 {actual_booker} 占位符。

    返回:
        {实际订舱客户: 输出文件 Path} 的映射。
    """
    input_file = Path(input_path)
    if not input_file.exists():
        raise FileNotFoundError(f"未找到输入 Excel 文件: {input_path}")

    df = pd.read_excel(input_file)

    if consigner_value:
        if consigner_field not in df.columns:
            raise KeyError(f"缺少列: {consigner_field}")
        df = df[df[consigner_field] == consigner_value]

    if actual_booker_field not in df.columns:
        raise KeyError(f"缺少列: {actual_booker_field}")

    if actual_booker_exclude:
        df = df[df[actual_booker_field] != actual_booker_exclude]

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    outputs: Dict[str, Path] = {}
    for actual_booker, group in df.groupby(actual_booker_field, dropna=False):
        actual_booker_name = "" if pd.isna(actual_booker) else str(actual_booker)
        if not actual_booker_name.strip():
            logger.warning("跳过空实际订舱客户分组")
            continue

        safe_name = _sanitize_filename(actual_booker_name)
        file_name = output_template.format(actual_booker=safe_name)
        output_path = output_root / file_name
        group.to_excel(output_path, index=False, sheet_name=sheet_name, engine="openpyxl")
        outputs[actual_booker_name] = output_path
        logger.info("实际订舱客户=%s 导出行数=%s -> %s", actual_booker_name, len(group), output_path)

    return outputs

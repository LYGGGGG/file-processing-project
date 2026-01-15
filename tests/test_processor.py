from pathlib import Path

import pandas as pd

from processor import split_excel_by_booker


def test_split_excel_by_booker_filters_and_sanitizes(tmp_path: Path) -> None:
    data = pd.DataFrame(
        [
            {"委托客户": "目标客户", "实际订舱客户": "A/B", "值": 1},
            {"委托客户": "目标客户", "实际订舱客户": "C", "值": 2},
            {"委托客户": "其他客户", "实际订舱客户": "D", "值": 3},
        ]
    )
    input_path = tmp_path / "input.xlsx"
    data.to_excel(input_path, index=False, engine="openpyxl")

    outputs = split_excel_by_booker(
        input_path=str(input_path),
        output_dir=str(tmp_path / "out"),
        consigner_field="委托客户",
        consigner_value="目标客户",
        actual_booker_field="实际订舱客户",
        output_template="{actual_booker}.xlsx",
    )

    assert set(outputs.keys()) == {"A/B", "C"}
    assert outputs["A/B"].name == "A_B.xlsx"
    assert outputs["C"].name == "C.xlsx"
    assert outputs["A/B"].exists()
    assert outputs["C"].exists()


def test_split_excel_by_booker_excludes_booker(tmp_path: Path) -> None:
    data = pd.DataFrame(
        [
            {"委托客户": "目标客户", "实际订舱客户": "A", "值": 1},
            {"委托客户": "目标客户", "实际订舱客户": "B", "值": 2},
        ]
    )
    input_path = tmp_path / "input.xlsx"
    data.to_excel(input_path, index=False, engine="openpyxl")

    outputs = split_excel_by_booker(
        input_path=str(input_path),
        output_dir=str(tmp_path / "out"),
        consigner_field="委托客户",
        consigner_value="目标客户",
        actual_booker_field="实际订舱客户",
        actual_booker_exclude="B",
    )

    assert set(outputs.keys()) == {"A"}

from __future__ import annotations

import csv
from pathlib import Path

from sanmar_netsuite.sanmar.parsers import parse_styles
from sanmar_netsuite.transform.csv_export import (
    CHILD_MATRIX_TYPE,
    CSV_COLUMNS,
    DEFAULT_INCOME_ACCOUNT,
    DEFAULT_TAX_SCHEDULE,
    write_matrix_csv,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_sdl_n.csv"


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_tax_schedule_column_present_and_defaults_to_taxable(tmp_path: Path) -> None:
    assert "Tax Schedule" in CSV_COLUMNS
    out = write_matrix_csv(parse_styles(FIXTURE), tmp_path / "matrix.csv")
    rows = _read(out)
    assert rows, "expected at least one SKU row"
    assert all(r["Tax Schedule"] == DEFAULT_TAX_SCHEDULE == "Taxable" for r in rows)


def test_tax_schedule_is_overridable(tmp_path: Path) -> None:
    out = write_matrix_csv(
        parse_styles(FIXTURE), tmp_path / "matrix.csv", tax_schedule="S2 - Taxable"
    )
    rows = _read(out)
    assert all(r["Tax Schedule"] == "S2 - Taxable" for r in rows)


def test_rows_are_child_matrix_items_linked_to_parent_style(tmp_path: Path) -> None:
    # Each row must be a child matrix item nested under its parent style, so
    # NetSuite's import nests them instead of creating standalone orphan items.
    for col in ("Matrix Type", "Subitem of"):
        assert col in CSV_COLUMNS
    out = write_matrix_csv(parse_styles(FIXTURE), tmp_path / "matrix.csv")
    rows = _read(out)
    assert all(r["Matrix Type"] == CHILD_MATRIX_TYPE == "Child Matrix Item" for r in rows)
    # 'Subitem of' (the parent link) must be the style, never the full child name.
    assert all(r["Subitem of"] and r["Subitem of"] != r["Item Name"] for r in rows)


def test_income_account_defaults_and_overrides(tmp_path: Path) -> None:
    out = write_matrix_csv(parse_styles(FIXTURE), tmp_path / "a.csv")
    assert all(r["Income Account"] == DEFAULT_INCOME_ACCOUNT for r in _read(out))
    out2 = write_matrix_csv(parse_styles(FIXTURE), tmp_path / "b.csv", income_account="4100")
    assert all(r["Income Account"] == "4100" for r in _read(out2))

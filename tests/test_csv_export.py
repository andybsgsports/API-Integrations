from __future__ import annotations

import csv
from pathlib import Path

from sanmar_netsuite.sanmar.parsers import parse_styles
from sanmar_netsuite.transform.csv_export import (
    CSV_COLUMNS,
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

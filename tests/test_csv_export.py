from __future__ import annotations

import csv
from pathlib import Path

from sanmar_netsuite.sanmar.parsers import parse_styles
from sanmar_netsuite.transform.csv_export import (
    CHILD_MATRIX_TYPE,
    CSV_COLUMNS,
    DEFAULT_INCOME_ACCOUNT,
    DEFAULT_TAX_SCHEDULE,
    class_for_category,
    write_matrix_csv,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_sdl_n.csv"


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_rows_are_child_matrix_items_nested_under_parent_style(tmp_path: Path) -> None:
    # Each row must nest under its parent style so NetSuite's import does not
    # create standalone orphan items.
    for col in ("Parent/Child Matrix Item", "Subitem Of", "Item Name/Number"):
        assert col in CSV_COLUMNS
    rows = _read(write_matrix_csv(parse_styles(FIXTURE), tmp_path / "m.csv"))
    assert rows
    assert CHILD_MATRIX_TYPE == "Child Matrix Item"
    assert all(r["Parent/Child Matrix Item"] == CHILD_MATRIX_TYPE for r in rows)
    # 'Subitem Of' (parent link) and 'Item Name/Number' are both the style.
    assert all(r["Subitem Of"] == r["Item Name/Number"] for r in rows)


def test_display_name_is_title_without_color_or_size(tmp_path: Path) -> None:
    rows = _read(write_matrix_csv(parse_styles(FIXTURE), tmp_path / "m.csv"))
    for r in rows:
        assert r["Matrix Attribute 2 - Color"] not in r["Display Name/Code"]
        assert r["Matrix Attribute 1 - Size"] not in r["Display Name/Code"]


def test_vendor_code_is_style_and_base_price_is_msrp(tmp_path: Path) -> None:
    rows = _read(write_matrix_csv(parse_styles(FIXTURE), tmp_path / "m.csv"))
    assert all(r["Vendor Name/Code"] == r["Item Name/Number"] for r in rows)
    # K420 sample MSRP is 18.00; Base Price must reflect MSRP, not piece price.
    k420 = [r for r in rows if r["Item Name/Number"] == "K420"]
    assert k420 and all(r["Base Price"] == "18.00" for r in k420)


def test_class_maps_from_category() -> None:
    assert class_for_category("Knit Shirts") == "Tops : Polos"
    assert class_for_category("Tee Shirts") == "Tops : Tees"
    assert class_for_category("Activewear") == "Tops"
    assert class_for_category("something unmapped") == ""


def test_accounts_and_tax_default_and_override(tmp_path: Path) -> None:
    rows = _read(write_matrix_csv(parse_styles(FIXTURE), tmp_path / "a.csv"))
    # Accounts are referenced by number so NetSuite's CSV import resolves them.
    assert DEFAULT_INCOME_ACCOUNT == "4100"
    assert all(r["Income Account"] == DEFAULT_INCOME_ACCOUNT for r in rows)
    assert all(r["COGS Account"] == "5100" and r["Asset Account"] == "1200" for r in rows)
    assert all(r["Tax Schedule"] == DEFAULT_TAX_SCHEDULE == "Taxable" for r in rows)
    rows2 = _read(
        write_matrix_csv(parse_styles(FIXTURE), tmp_path / "b.csv", income_account="4150")
    )
    assert all(r["Income Account"] == "4150" for r in rows2)

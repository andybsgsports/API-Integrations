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
    # 'Subitem Of' is the parent style; 'Item Name/Number' is a unique child name
    # that starts with the style. They must NOT be identical (else uniqueness
    # errors after the first child imports).
    assert all(r["Item Name/Number"].startswith(r["Subitem Of"] + "-") for r in rows)
    assert len({r["Item Name/Number"] for r in rows}) == len(rows)


def test_display_name_is_title_without_color_or_size(tmp_path: Path) -> None:
    rows = _read(write_matrix_csv(parse_styles(FIXTURE), tmp_path / "m.csv"))
    for r in rows:
        assert r["Matrix Attribute 2 - Color"] not in r["Display Name/Code"]
        assert r["Matrix Attribute 1 - Size"] not in r["Display Name/Code"]


def test_vendor_code_is_style(tmp_path: Path) -> None:
    rows = _read(write_matrix_csv(parse_styles(FIXTURE), tmp_path / "m.csv"))
    # Vendor Name/Code is the style (== Subitem Of), not the unique child name.
    assert all(r["Vendor Name/Code"] == r["Subitem Of"] for r in rows)
    # Pricing sublist columns are absent — prices are applied post-import by reconcile-items.
    assert "Price" not in rows[0]
    assert "Price Level" not in rows[0]
    assert "Currency" not in rows[0]


def test_numeric_style_uses_parent_internal_id_ref(tmp_path: Path) -> None:
    # Numeric styles (e.g. 2000) must reference the parent by internal id, while
    # alphanumeric styles stay referenced by name.
    rows = _read(
        write_matrix_csv(parse_styles(FIXTURE), tmp_path / "m.csv", parent_refs={"2000": "100102"})
    )
    two_thousand = [r for r in rows if r["Vendor Name/Code"] == "2000"]
    assert two_thousand and all(r["Subitem Of"] == "100102" for r in two_thousand)
    k420 = [r for r in rows if r["Vendor Name/Code"] == "K420"]
    assert k420 and all(r["Subitem Of"] == "K420" for r in k420)


def test_skip_external_ids_omits_those_rows(tmp_path: Path) -> None:
    from sanmar_netsuite.netsuite.repository import child_external_id

    styles = parse_styles(FIXTURE)
    skip_one = next(child_external_id(sku.unique_key) for s in styles for sku in s.skus)
    rows = _read(write_matrix_csv(styles, tmp_path / "m.csv", skip_external_ids={skip_one}))
    assert rows and all(r["External ID"] != skip_one for r in rows)


def test_class_maps_from_category() -> None:
    assert class_for_category("Knit Shirts") == "Tops : Polos"
    assert class_for_category("Tee Shirts") == "Tops : Tees"
    assert class_for_category("Activewear") == "Tops"
    assert class_for_category("something unmapped") == ""


def test_class_maps_multi_tag_category() -> None:
    """SanMar's CATEGORY_NAME is a semicolon-delimited list mixing garment
    type with audience/segment tags (real values from the live feed) -- the
    exact-match table alone leaves ~46% of SKUs with a blank Class."""
    assert class_for_category("T-Shirts ;Tall;Activewear") == "Tops : Tees"
    assert class_for_category("Outerwear;Women's") == "Outerwear : Jackets"
    assert class_for_category("Sweatshirts/Fleece;Women's") == "Tops : Sweatshirts"
    assert class_for_category("Polos/Knits;Women's") == "Tops : Polos"
    assert class_for_category("Caps;Youth") == "Uniforms : Headwear"
    assert class_for_category("Youth;Caps") == "Uniforms : Headwear"
    assert class_for_category("Women's;Woven Shirts") == "Tops"
    assert class_for_category("Activewear;T-Shirts ;Women's") == "Tops : Tees"


def test_class_leaves_non_garment_tags_unmapped() -> None:
    """Categories made entirely of audience/segment/PPE tags with no garment
    type are left blank rather than guessed."""
    assert class_for_category("Workwear;Bottoms") == ""
    assert class_for_category("Personal Protection;Workwear") == ""
    assert class_for_category("Accessories") == ""
    assert class_for_category("Infant & Toddler") == ""
    assert class_for_category("Women's") == ""
    assert class_for_category("") == ""


def test_accounts_and_tax_default_and_override(tmp_path: Path) -> None:
    rows = _read(write_matrix_csv(parse_styles(FIXTURE), tmp_path / "a.csv"))
    # Accounts are referenced by number (BSG's import map resolves numbers).
    assert DEFAULT_INCOME_ACCOUNT == "4100"
    assert all(r["Income Account"] == DEFAULT_INCOME_ACCOUNT for r in rows)
    assert all(r["COGS Account"] == "5100" and r["Asset Account"] == "1200" for r in rows)
    assert all(r["Tax Schedule"] == DEFAULT_TAX_SCHEDULE == "Taxable" for r in rows)
    rows2 = _read(
        write_matrix_csv(parse_styles(FIXTURE), tmp_path / "b.csv", income_account="4150")
    )
    assert all(r["Income Account"] == "4150" for r in rows2)

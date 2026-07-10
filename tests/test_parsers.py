from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sanmar_netsuite.sanmar.parsers import parse_inventory, parse_styles


def test_parse_styles_groups_skus_by_style(sdl_n_path):
    styles = {s.style: s for s in parse_styles(sdl_n_path)}
    assert set(styles) == {"K420", "2000"}

    k420 = styles["K420"]
    assert k420.title == "Port Authority Silk Touch Polo"
    assert k420.brand == "Port Authority"
    assert k420.category == "Knit Shirts"
    # 2 colors x 2 sizes
    assert len(k420.skus) == 4
    assert k420.colors == ["Black", "Classic Navy"]
    assert k420.sizes == ["S", "M"]


def test_parse_styles_decimal_and_int_coercion(sdl_n_path):
    k420 = next(s for s in parse_styles(sdl_n_path) if s.style == "K420")
    black_s = next(
        sku for sku in k420.skus if sku.color_name == "Black" and sku.size == "S"
    )
    assert black_s.unique_key == "920331"
    assert black_s.inventory_key == "92033"
    assert black_s.size_index == "1"
    assert black_s.piece_price == Decimal("9.99")
    assert black_s.case_price == Decimal("8.49")
    assert black_s.case_size == 36
    assert black_s.msrp == Decimal("18.00")
    assert black_s.map_price == Decimal("16.20")
    assert black_s.gtin == "00882849000011"
    assert black_s.mainframe_color == "Black"


def test_parse_styles_collects_images_per_color(sdl_n_path):
    k420 = next(s for s in parse_styles(sdl_n_path) if s.style == "K420")
    navy = k420.images_by_color["Classic Navy"]
    assert navy.front_model_url.endswith("navy_model_front.jpg")
    assert navy.back_flat_url.endswith("navy_flat_back.jpg")
    assert navy.primary_url().endswith("navy_model_front.jpg")
    # Discontinued style with only front images still resolves a primary url.
    tee = next(s for s in parse_styles(sdl_n_path) if s.style == "2000")
    assert tee.images_by_color["White"].primary_url().endswith("white_model_front.jpg")


def test_parse_styles_marks_discontinued(sdl_n_path):
    tee = next(s for s in parse_styles(sdl_n_path) if s.style == "2000")
    assert tee.product_status == "Discontinued"
    assert tee.skus[0].is_discontinued is True


def test_parse_inventory_aggregates_warehouses(dip_path):
    records = {r.unique_key: r for r in parse_inventory(dip_path)}
    # 920331 has stock in two warehouses (Seattle=120, Dallas=45)
    black_s = records["920331"]
    assert black_s.total_qty == 165
    whse = {w.warehouse_no: w.quantity for w in black_s.warehouses}
    assert whse == {"1": 120, "3": 45}
    assert black_s.warehouses[0].warehouse_label == "Seattle, WA"


def test_parse_inventory_skips_header_row(dip_path, tmp_path):
    # SanMar's live dip export includes a column-header line (col 5 == "whse_no").
    # It must be skipped, not parsed as data — otherwise int("whse_no") crashes.
    header = "|".join("whse_no" if i == 5 else f"col{i}" for i in range(19))
    body = Path(dip_path).read_text(encoding="utf-8-sig")
    dip = tmp_path / "dip_with_header.txt"
    dip.write_text(header + "\n" + body, encoding="utf-8")

    records = {r.unique_key: r for r in parse_inventory(dip)}
    # Real data still parsed, and no bogus "whse_no" warehouse leaked in.
    assert records["920331"].total_qty == 165
    all_whses = {w.warehouse_no for r in records.values() for w in r.warehouses}
    assert "whse_no" not in all_whses


def test_parse_inventory_sale_pricing(dip_path):
    records = {r.unique_key: r for r in parse_inventory(dip_path)}
    black_s = records["920331"]
    assert black_s.piece_price == Decimal("9.99")
    assert black_s.each_sale_price == Decimal("8.99")
    assert black_s.sale_start == "2026-06-01 00:00:00"
    # SKU with no sale leaves sale price empty
    navy_s = records["1959911"]
    assert navy_s.each_sale_price is None
    assert navy_s.total_qty == 85  # 75 + 10


def test_parse_inventory_discontinued_code(dip_path):
    records = {r.unique_key: r for r in parse_inventory(dip_path)}
    tee = records["258524"]
    assert tee.discontinued_code == "S"
    assert tee.total_qty == 0

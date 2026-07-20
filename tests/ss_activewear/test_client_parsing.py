"""Parser-level tests for the S&S API client.

These do not hit the network — they only verify ``product_from_payload`` and
``style_from_payload`` correctly coerce the JSON shapes we expect from S&S.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from ss_activewear_netsuite.ss_activewear.client import (
    product_from_payload,
    style_from_payload,
)


def test_product_from_payload_basic(ss_products_path: Path) -> None:
    rows = json.loads(ss_products_path.read_text())
    products = [product_from_payload(r) for r in rows]
    assert len(products) == 4

    g500_s = products[0]
    assert g500_s.sku == "B00120001"
    assert g500_s.style_id == "100"
    assert g500_s.style_name == "G500"
    assert g500_s.brand_name == "Gildan"
    assert g500_s.color_name == "White"
    assert g500_s.size_name == "S"
    assert g500_s.size_order == 1
    assert g500_s.piece_price == Decimal("3.49")
    assert g500_s.case_size == 72
    assert g500_s.qty_available == 1234
    assert len(g500_s.warehouses) == 3
    assert g500_s.warehouses[0].warehouse_abbr == "IL"
    assert g500_s.warehouses[0].qty == 600
    assert g500_s.is_closeout is False
    assert g500_s.is_discontinued is False


def test_product_from_payload_with_sale_and_map(ss_products_path: Path) -> None:
    rows = json.loads(ss_products_path.read_text())
    pa55 = product_from_payload(rows[2])
    assert pa55.sku == "B00187730"
    assert pa55.sale_price == Decimal("7.99")
    assert pa55.map_price == Decimal("15.99")
    assert pa55.msrp == Decimal("22.00")


def test_product_from_payload_handles_discontinued_and_empty_warehouses(
    ss_products_path: Path,
) -> None:
    rows = json.loads(ss_products_path.read_text())
    dt6000 = product_from_payload(rows[3])
    assert dt6000.is_discontinued is True
    assert dt6000.qty_available == 0
    assert dt6000.warehouses == ()
    assert dt6000.gtin == ""


def test_product_from_payload_tolerates_missing_fields() -> None:
    p = product_from_payload({"sku": "X"})
    assert p.sku == "X"
    assert p.style_id == ""
    assert p.qty_available == 0
    assert p.piece_price is None


def test_style_from_payload() -> None:
    s = style_from_payload(
        {
            "styleID": 42,
            "styleName": "Z100",
            "brandName": "Acme",
            "title": "Acme Z100 Tee",
            "categoryName": "T-Shirts",
        }
    )
    assert s.style_id == "42"
    assert s.style_name == "Z100"
    assert s.brand_name == "Acme"
    assert s.title == "Acme Z100 Tee"


def test_product_from_payload_live_api_keys() -> None:
    """The real /Products payload keys differ from the early fixture names:
    unitWeight / caseQty / retailPrice (verified against the live API), and
    mapPrice 0.01 is S&S's "no MAP restriction" placeholder."""
    p = product_from_payload({
        "sku": "B06560535",
        "styleName": "8000",
        "unitWeight": 0.4583,
        "caseQty": 72,
        "retailPrice": 6.9,
        "mapPrice": 0.01,
        "piecePrice": 3.45,
        "customerPrice": 2.66,
    })
    assert p.weight == Decimal("0.4583")
    assert p.case_size == 72
    assert p.msrp == Decimal("6.9")
    assert p.map_price is None  # 0.01 placeholder -> no MAP
    assert p.customer_price == Decimal("2.66")


def test_product_from_payload_real_map_survives() -> None:
    p = product_from_payload({"sku": "X", "mapPrice": 15.99})
    assert p.map_price == Decimal("15.99")

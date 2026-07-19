"""Parse Momentec's standard product-data CSV feeds.

The same column layout is used by every product-data file family
(``product-data-std-all.csv``, the sublimation variant, ...), per the ASG
Standard Data Feed Specification. Files are fetched from Momentec's public
static host (``static.momentecbrands.com/productdata/``) — configure via
``MOMENTEC_FEED_BASE_URL`` and parse with :func:`parse_product_data`.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from pathlib import Path

from .models import MomentecSku, MomentecStyle


def _clean(v: str | None) -> str:
    return (v or "").strip()


def iter_product_rows(path: Path) -> Iterator[MomentecSku]:
    """Yield one :class:`MomentecSku` per feed row (skips rows w/o Item_SKU)."""
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh):
            item_sku = _clean(row.get("Item_SKU"))
            if not item_sku:
                continue
            yield MomentecSku(
                parent_sku=_clean(row.get("Parent_SKU")),
                item_sku=item_sku,
                upc=_clean(row.get("UPC_Code")),
                gtin=_clean(row.get("GTIN")),
                name=_clean(row.get("Item_Name")),
                brand=_clean(row.get("Brand")),
                division=_clean(row.get("Division")),
                description=_clean(row.get("Item_Description")),
                category=_clean(row.get("Category")),
                msrp=_clean(row.get("MSRP")),
                cost=_clean(row.get("Cost")),
                currency=_clean(row.get("Currency")),
                color=_clean(row.get("Color")),
                size=_clean(row.get("Size")),
                color_hex=_clean(row.get("Color_Hex_Value")),
                status=_clean(row.get("Status")),
                main_image_url=_clean(row.get("Main_Image_URL")),
                swatch_image_url=_clean(row.get("Swatch_Image_URL")),
                weight=_clean(row.get("Weight")),
                case_pack_qty=_clean(row.get("Case_Pack_Qty")),
                country_of_origin=_clean(row.get("Country_Of_Origin")),
                size_chart_url=_clean(row.get("Size_Chart_Image_URL")),
            )


def parse_product_data(paths: Iterable[Path]) -> list[MomentecStyle]:
    """Group feed rows from one or more files into styles (matrix parents)."""
    styles: dict[str, MomentecStyle] = {}
    for path in paths:
        for sku in iter_product_rows(path):
            style = styles.setdefault(
                sku.parent_sku,
                MomentecStyle(
                    parent_sku=sku.parent_sku, name=sku.name,
                    brand=sku.brand, category=sku.category,
                ),
            )
            style.skus.append(sku)
    return list(styles.values())

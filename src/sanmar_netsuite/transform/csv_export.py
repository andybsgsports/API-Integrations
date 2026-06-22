"""Generate a NetSuite CSV-import file for the initial matrix-item load.

Creating the matrix parent/child structure is the one operation that is far more
reliable through NetSuite's **CSV Import Assistant** than the REST record API.
This module writes a single CSV — one row per SanMar SKU — with the columns a
matrix-item import map expects. NetSuite creates the parent automatically from
the ``Parent`` reference and the matrix option columns (Color, Size).

After this one-time load, the REST syncs
(:mod:`sanmar_netsuite.sync`) keep prices, availability, status, and images
current by external id.

Import setup notes live in ``docs/NETSUITE_SETUP.md``.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from ..models import StyleRecord
from .catalog import _primary_image_url  # reuse the primary-image picker
from .sizes import normalize_size

# NetSuite requires a Tax Schedule on inventory items. SanMar apparel is a
# taxable good, so every row carries the account's taxable schedule by default;
# override per account with SYNC_TAX_SCHEDULE.
DEFAULT_TAX_SCHEDULE = "Taxable"

CSV_COLUMNS = [
    "External ID",  # SANMAR-<unique_key>
    "Parent External ID",  # SANMAR-<style>
    "Item Name",  # style:color:size
    "Parent Item Name",  # style
    "Display Name",
    "Description",
    "Brand",
    "Category",
    "Subcategory",
    "Color",  # matrix option
    "Size",  # matrix option
    "Mainframe Color",
    "SanMar Unique Key",
    "SanMar Inventory Key",
    "SanMar Size Index",
    "GTIN/UPC",
    "Base Price",  # piece price
    "Case Price",
    "Case Size",
    "MSRP",
    "MAP",
    "Weight (lb)",
    "Product Status",
    "Tax Schedule",  # required by NetSuite for inventory items
    "Image URL",
]


def _row(sku, style: StyleRecord, tax_schedule: str) -> dict[str, str]:
    from ..netsuite.repository import child_external_id, parent_external_id

    color_img = style.images_by_color.get(sku.color_name)
    image_url = color_img.primary_url() if color_img else _primary_image_url(style)
    size = normalize_size(sku.size)
    return {
        "External ID": child_external_id(sku.unique_key),
        "Parent External ID": parent_external_id(sku.style),
        "Item Name": f"{sku.style}:{sku.mainframe_color or sku.color_name}:{size}",
        "Parent Item Name": sku.style,
        "Display Name": f"{style.title} - {sku.color_name} - {size}"[:60],
        "Description": sku.description or style.description,
        "Brand": style.brand,
        "Category": style.category,
        "Subcategory": style.subcategory,
        "Color": sku.color_name,
        "Size": size,
        "Mainframe Color": sku.mainframe_color,
        "SanMar Unique Key": sku.unique_key,
        "SanMar Inventory Key": sku.inventory_key,
        "SanMar Size Index": sku.size_index,
        "GTIN/UPC": sku.gtin,
        "Base Price": _num(sku.piece_price),
        "Case Price": _num(sku.case_price),
        "Case Size": str(sku.case_size) if sku.case_size is not None else "",
        "MSRP": _num(sku.msrp),
        "MAP": _num(sku.map_price),
        "Weight (lb)": _num(sku.piece_weight),
        "Product Status": sku.product_status,
        "Tax Schedule": tax_schedule,
        "Image URL": image_url,
    }


def _num(value) -> str:
    return "" if value is None else str(value)


def write_matrix_csv(
    styles: Iterable[StyleRecord],
    out_path: str | Path,
    *,
    tax_schedule: str = DEFAULT_TAX_SCHEDULE,
) -> Path:
    """Write a matrix-item import CSV for all SKUs across ``styles``.

    ``tax_schedule`` is written verbatim into the required NetSuite Tax Schedule
    column for every row (defaults to the account's taxable schedule).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for style in styles:
            for sku in style.skus:
                writer.writerow(_row(sku, style, tax_schedule))
                count += 1
    return out_path

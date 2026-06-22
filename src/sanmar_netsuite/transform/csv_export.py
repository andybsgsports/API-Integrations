"""Generate a NetSuite CSV-import file for the matrix-item load.

Creating matrix parent/child structure is far more reliable through NetSuite's
**CSV Import Assistant** than the REST record API — the REST API cannot create
true matrix children at all (the matrix option fields are read-only/derived;
NetSuite only generates children through the UI or this import).

Per NetSuite's matrix-import rules, every child row must carry:

* ``Matrix Type`` = ``Child Matrix Item``
* ``Subitem of`` = the parent matrix item's name (the style)
* ``Color`` / ``Size`` values that match the matrix custom lists

and the parent matrix item must already list those colors/sizes in its grid
(handled beforehand by :mod:`sanmar_netsuite.netsuite.matrix_grid`). After this
load, the REST syncs (:mod:`sanmar_netsuite.sync`) keep prices, availability,
status, and images current by external id.

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

# Income account for the items. SanMar items are merchandise, so they post to
# the merchandise-sales account by default; override with SYNC_INCOME_ACCOUNT.
DEFAULT_INCOME_ACCOUNT = "SALES OF MERCHANDISE"

# NetSuite matrix-type value that marks each row as a child of its parent style.
CHILD_MATRIX_TYPE = "Child Matrix Item"

CSV_COLUMNS = [
    "External ID",  # SANMAR-<unique_key>
    "Matrix Type",  # "Child Matrix Item" — tells NetSuite to nest, not orphan
    "Item Name",  # the child's own name (style-color-size)
    "Subitem of",  # parent matrix item name (the style) — the nesting link
    "Display Name",
    "Description",
    "Brand",
    "Category",
    "Subcategory",
    "Color",  # matrix option — must match the color custom list
    "Size",  # matrix option — must match the size custom list
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
    "Income Account",
    "Image URL",
]


def _child_item_name(style: str, color: str, size: str) -> str:
    """The child sub-item's own Item Name/Number, e.g. ``K420-Classic Navy-Small``."""
    return f"{style}-{color}-{size}"


def _row(sku, style: StyleRecord, *, tax_schedule: str, income_account: str) -> dict[str, str]:
    from ..netsuite.repository import child_external_id

    color_img = style.images_by_color.get(sku.color_name)
    image_url = color_img.primary_url() if color_img else _primary_image_url(style)
    size = normalize_size(sku.size)
    return {
        "External ID": child_external_id(sku.unique_key),
        "Matrix Type": CHILD_MATRIX_TYPE,
        "Item Name": _child_item_name(sku.style, sku.color_name, size),
        "Subitem of": sku.style,
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
        "Income Account": income_account,
        "Image URL": image_url,
    }


def _num(value) -> str:
    return "" if value is None else str(value)


def write_matrix_csv(
    styles: Iterable[StyleRecord],
    out_path: str | Path,
    *,
    tax_schedule: str = DEFAULT_TAX_SCHEDULE,
    income_account: str = DEFAULT_INCOME_ACCOUNT,
) -> Path:
    """Write a matrix child-item import CSV for all SKUs across ``styles``.

    Each row is a child matrix item (``Matrix Type`` = "Child Matrix Item")
    linked to its parent style via ``Subitem of``. ``tax_schedule`` and
    ``income_account`` are written verbatim into every row.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for style in styles:
            for sku in style.skus:
                writer.writerow(
                    _row(sku, style, tax_schedule=tax_schedule, income_account=income_account)
                )
                count += 1
    return out_path

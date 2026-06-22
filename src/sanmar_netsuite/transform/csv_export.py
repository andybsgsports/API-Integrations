"""Generate a NetSuite CSV-import file for the matrix child-item load.

Matrix structure must be created through NetSuite's **CSV Import Assistant** —
the REST API cannot create true matrix children (the matrix option fields are
read-only/derived). This module emits one row per SanMar SKU in BSG's matrix
import-template format: each row is a *child* matrix item linked to its parent
style, with the columns and conventions taken from BSG's live items.

Per NetSuite's matrix-import rules, every child row carries:

* ``Parent/Child Matrix Item`` = ``Child Matrix Item``
* ``Subitem Of`` = the parent matrix item's name (the style)
* ``Matrix Attribute 1 - Size`` / ``Matrix Attribute 2 - Color`` matching the
  matrix custom lists

The parent matrix item must already exist and list those colors/sizes in its
grid. After this load, the REST syncs keep prices, availability, and status
current by external id.

Import setup notes live in ``docs/NETSUITE_SETUP.md``.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

from ..models import StyleRecord
from .sizes import normalize_size

# SanMar category (lowercased) -> NetSuite Class ("Parent : Child"). Anything
# not listed maps to an empty Class (left for manual assignment in NetSuite).
CATEGORY_TO_CLASS = {
    "tee shirts": "Tops : Tees",
    "t-shirts": "Tops : Tees",
    "knit shirts": "Tops : Polos",
    "polos/knits": "Tops : Polos",
    "sweatshirts/fleece": "Tops : Sweatshirts",
    "sweatshirts": "Tops : Sweatshirts",
    "outerwear": "Outerwear : Jackets",
    "activewear": "Tops",
    "woven shirts": "Tops",
    "caps": "Uniforms : Headwear",
    "headwear": "Uniforms : Headwear",
    "bags": "Bags",
    "pants": "Bottoms : Pants",
    "shorts": "Bottoms : Shorts",
}

# Accounts/tax are configurable (see SyncConfig); the rest are BSG-standard
# defaults taken from their live item records. Accounts are referenced by
# NUMBER — BSG's import map resolves account numbers (verified against a
# successful import).
DEFAULT_INCOME_ACCOUNT = "4100"  # SALES OF MERCHANDISE
DEFAULT_COGS_ACCOUNT = "5100"  # COST OF MERCHANDISE SOLD
DEFAULT_ASSET_ACCOUNT = "1200"  # INVENTORY
DEFAULT_TAX_SCHEDULE = "Taxable"
DEFAULT_SUBSIDIARY = "Parent Company : Badger Sporting Goods Company"
DEFAULT_DEPARTMENT = "Apparel"
DEFAULT_LOCATION = "Badger Sporting Goods"
DEFAULT_COSTING_METHOD = "Average"
DEFAULT_VENDOR = "Sanmar Corp"  # exact NetSuite vendor entity name

CHILD_MATRIX_TYPE = "Child Matrix Item"

# BSG matrix import-template columns, in order.
CSV_COLUMNS = [
    "External ID",
    "Item Name/Number",  # unique child name: style-color-size (must be unique)
    "Display Name/Code",  # product title only — no color/size
    "Vendor Name/Code",  # the vendor's code for the item = the style
    "Parent/Child Matrix Item",
    "Subitem Of",  # parent matrix item name (the style) — the nesting link
    "Matrix Attribute 1 - Size",
    "Matrix Attribute 2 - Color",
    "UPC Code",
    "Description",
    "Units Type",
    "Stock Units",
    "Purchase Units",
    "Sale Units",
    "Subsidiary",
    "Include Children",
    "Department",
    "Class",
    "Location",
    "Costing Method",
    "Purchase Price",  # SanMar's piece price (our cost)
    "Vendor 1 Name",
    "Vendor 1 Purchase Price",
    # Pricing sublist columns intentionally omitted: NetSuite's matrix child
    # importer rejects them with "Please enter missing price(s)" regardless of
    # format. Prices are applied after import by `reconcile-items` via REST.
    "Weight",
    "COGS Account",
    "Income Account",
    "Asset Account",
    "Tax Schedule",
]


def class_for_category(category: str) -> str:
    """Map a SanMar category to a NetSuite Class path, or '' if unmapped."""
    return CATEGORY_TO_CLASS.get((category or "").strip().lower(), "")


def _child_item_name(style: str, color: str, size: str) -> str:
    """The child sub-item's own (unique) Item Name/Number, e.g. ``K420-Black-Small``.

    Each child needs a unique name; sharing the parent style across rows triggers
    NetSuite uniqueness errors after the first child imports.
    """
    return f"{style}-{color}-{size}"


def _num(value: Decimal | int | None) -> str:
    return "" if value is None else str(value)


def _row(
    sku,
    style: StyleRecord,
    *,
    income_account: str,
    cogs_account: str,
    asset_account: str,
    tax_schedule: str,
    parent_refs: dict[str, str],
) -> dict[str, str]:
    from ..netsuite.repository import child_external_id

    size = normalize_size(sku.size)
    cost = _num(sku.piece_price)
    # Numeric styles are referenced by parent internal id (see netsuite.parents);
    # everything else by the style name.
    subitem_of = parent_refs.get(sku.style, sku.style)
    return {
        "External ID": child_external_id(sku.unique_key),
        "Item Name/Number": _child_item_name(sku.style, sku.color_name, size),
        "Display Name/Code": style.title[:60],
        "Vendor Name/Code": sku.style,
        "Parent/Child Matrix Item": CHILD_MATRIX_TYPE,
        "Subitem Of": subitem_of,
        "Matrix Attribute 1 - Size": size,
        "Matrix Attribute 2 - Color": sku.color_name,
        "UPC Code": sku.gtin,
        "Description": sku.description or style.description,
        "Units Type": "Each",
        "Stock Units": "Eaches",
        "Purchase Units": "Eaches",
        "Sale Units": "Eaches",
        "Subsidiary": DEFAULT_SUBSIDIARY,
        "Include Children": "TRUE",
        "Department": DEFAULT_DEPARTMENT,
        "Class": class_for_category(style.category),
        "Location": DEFAULT_LOCATION,
        "Costing Method": DEFAULT_COSTING_METHOD,
        "Purchase Price": cost,
        "Vendor 1 Name": DEFAULT_VENDOR,
        "Vendor 1 Purchase Price": cost,
        "Weight": _num(sku.piece_weight),
        "COGS Account": cogs_account,
        "Income Account": income_account,
        "Asset Account": asset_account,
        "Tax Schedule": tax_schedule,
    }


def write_matrix_csv(
    styles: Iterable[StyleRecord],
    out_path: str | Path,
    *,
    tax_schedule: str = DEFAULT_TAX_SCHEDULE,
    income_account: str = DEFAULT_INCOME_ACCOUNT,
    cogs_account: str = DEFAULT_COGS_ACCOUNT,
    asset_account: str = DEFAULT_ASSET_ACCOUNT,
    parent_refs: dict[str, str] | None = None,
    skip_external_ids: set[str] | None = None,
) -> Path:
    """Write a matrix child-item import CSV for all SKUs across ``styles``.

    Each row is a child matrix item linked to its parent style via ``Subitem Of``
    in BSG's import-template format. ``parent_refs`` maps a style name to the
    value to use for ``Subitem Of`` (used to reference parents by internal id);
    styles absent from it are referenced by name. ``skip_external_ids`` are
    children whose combo already exists in NetSuite and are left out.
    """
    refs = parent_refs or {}
    skip = skip_external_ids or set()
    from ..netsuite.repository import child_external_id

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for style in styles:
            for sku in style.skus:
                if child_external_id(sku.unique_key) in skip:
                    continue
                writer.writerow(
                    _row(
                        sku,
                        style,
                        income_account=income_account,
                        cogs_account=cogs_account,
                        asset_account=asset_account,
                        tax_schedule=tax_schedule,
                        parent_refs=refs,
                    )
                )
    return out_path

"""Matrix-item Import Assistant CSV writer for the initial NetSuite load.

NetSuite's REST API is unreliable at *building* matrix option lists but
solid at *updating* them. The recommended flow (same as SanMar) is:

1. Use this writer to generate a CSV of every S&S SKU (one row per child),
2. Import it once via *Setup → Import/Export → Import CSV Records* with
   record type Inventory Item and data handling Add or Update,
3. Use the REST sync commands for ongoing updates.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

from ..models import SsProduct
from .catalog import sku_external_id, style_external_id

CSV_HEADERS = [
    "External ID",
    "Parent External ID",
    "Item Name",
    "Parent Item Name",
    "Display Name",
    "Description",
    "Brand",
    "Category",
    "Color",
    "Size",
    "Color Code",
    "Size Order",
    "SS SKU",
    "SS Style ID",
    "GTIN/UPC",
    "Piece Price",
    "Dozen Price",
    "Case Price",
    "Case Size",
    "MSRP",
    "MAP",
    "Weight (lb)",
    "Closeout",
    "Discontinued",
    "Front Image URL",
    "On-Model Image URL",
]


def _num(value: Decimal | None) -> str:
    return f"{value:.2f}" if value is not None else ""


def _yesno(value: bool) -> str:
    return "Yes" if value else "No"


def write_csv(products: Iterable[SsProduct], out_path: Path) -> int:
    """Write ``products`` to a NetSuite-importable CSV. Returns row count."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows_written = 0
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for product in products:
            writer.writerow(
                {
                    "External ID": sku_external_id(product),
                    "Parent External ID": style_external_id(product),
                    "Item Name": (
                        f"{product.style_name}:{product.color_name}:{product.size_name}"
                    ),
                    "Parent Item Name": product.style_name,
                    "Display Name": " - ".join(
                        p
                        for p in (product.style_name, product.color_name, product.size_name)
                        if p
                    ),
                    "Description": product.description,
                    "Brand": product.brand_name,
                    "Category": product.category_name,
                    "Color": product.color_name,
                    "Size": product.size_name,
                    "Color Code": product.color_code,
                    "Size Order": product.size_order,
                    "SS SKU": product.sku,
                    "SS Style ID": product.style_id,
                    "GTIN/UPC": product.gtin,
                    "Piece Price": _num(product.piece_price),
                    "Dozen Price": _num(product.dozen_price),
                    "Case Price": _num(product.case_price),
                    "Case Size": product.case_size or "",
                    "MSRP": _num(product.msrp),
                    "MAP": _num(product.map_price),
                    "Weight (lb)": _num(product.weight),
                    "Closeout": _yesno(product.is_closeout),
                    "Discontinued": _yesno(product.is_discontinued),
                    "Front Image URL": product.front_image_url,
                    "On-Model Image URL": product.on_model_image_url,
                }
            )
            rows_written += 1
    return rows_written

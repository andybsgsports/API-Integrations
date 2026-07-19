"""Data model for Momentec's standard product-data feed.

One row per sellable SKU. ``Item_SKU`` is ``PARENT.COLORCODE.SIZECODE``
(e.g. ``029HBM.BLK.2XL``); ``Parent_SKU`` groups SKUs into a matrix parent.
``UPC_Code``/``GTIN`` carry the manufacturer barcode used to reconcile
against existing NetSuite items and against SanMar/S&S rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MomentecSku:
    parent_sku: str
    item_sku: str
    upc: str
    gtin: str
    name: str
    brand: str
    division: str
    description: str
    category: str
    msrp: str
    cost: str
    currency: str
    color: str
    size: str
    color_hex: str
    status: str
    main_image_url: str
    swatch_image_url: str
    weight: str
    case_pack_qty: str
    country_of_origin: str
    # Fit/size guide URL from the feed's Size_Chart_Image_URL column. Today
    # it's a single generic chart for every brand; kept per-SKU so brand- or
    # style-specific guides flow through automatically if the feed adds them.
    size_chart_url: str = ""

    @property
    def color_code(self) -> str:
        parts = self.item_sku.split(".")
        return parts[1] if len(parts) >= 3 else ""

    @property
    def size_code(self) -> str:
        parts = self.item_sku.split(".")
        return parts[2] if len(parts) >= 3 else ""


@dataclass
class MomentecStyle:
    parent_sku: str
    name: str = ""
    brand: str = ""
    category: str = ""
    skus: list[MomentecSku] = field(default_factory=list)

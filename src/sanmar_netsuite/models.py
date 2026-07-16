"""Domain models shared across parsing, transformation, and sync.

These are deliberately plain dataclasses (not tied to SanMar's file format or
NetSuite's payload shape) so the transform layer has a stable contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class SkuRecord:
    """One style/color/size combination — a single sellable SanMar SKU.

    Maps to a NetSuite *matrix child* item. ``unique_key`` (INVENTORY_KEY +
    SIZE_INDEX) is SanMar's stable identifier and our NetSuite external id.
    """

    unique_key: str
    inventory_key: str
    size_index: str
    style: str  # e.g. "K420"
    color_name: str  # full color, e.g. "Classic Navy"
    mainframe_color: str  # abbreviated color used for web-services orders
    size: str  # e.g. "XL"
    description: str
    piece_price: Decimal | None = None
    case_price: Decimal | None = None
    case_size: int | None = None
    piece_weight: Decimal | None = None
    msrp: Decimal | None = None
    map_price: Decimal | None = None
    gtin: str = ""
    product_status: str = ""
    available_qty: int | None = None  # all-warehouse total (EPDD QTY column)

    @property
    def is_discontinued(self) -> bool:
        from .sanmar.constants import STATUS_DISCONTINUED

        return self.product_status.strip().lower() == STATUS_DISCONTINUED.lower()


@dataclass(frozen=True)
class StyleRecord:
    """A SanMar style — the parent of one or more SKUs.

    Maps to a NetSuite *matrix parent* item. Image URLs live here because they
    are defined at the style/color level in the SDL/EPDD feeds.
    """

    style: str
    title: str  # PRODUCT_TITLE, includes manufacturer name
    description: str  # PRODUCT_DESCRIPTION
    brand: str  # MILL
    category: str  # CATEGORY_NAME
    subcategory: str  # SUBCATEGORY_NAME
    product_status: str
    skus: list[SkuRecord] = field(default_factory=list)
    # color_name -> set of image URLs (front/back model + flat)
    images_by_color: dict[str, ColorImages] = field(default_factory=dict)

    @property
    def colors(self) -> list[str]:
        seen: list[str] = []
        for sku in self.skus:
            if sku.color_name not in seen:
                seen.append(sku.color_name)
        return seen

    @property
    def sizes(self) -> list[str]:
        seen: list[str] = []
        for sku in self.skus:
            if sku.size not in seen:
                seen.append(sku.size)
        return seen


@dataclass(frozen=True)
class ColorImages:
    """Image URLs for a single color of a style (from SDL_N / EPDD)."""

    color_name: str
    front_model_url: str = ""
    back_model_url: str = ""
    front_flat_url: str = ""
    back_flat_url: str = ""
    color_swatch_url: str = ""

    def primary_url(self) -> str:
        """Best single image to represent the color: front model, else flat."""
        return self.front_model_url or self.front_flat_url or ""

    def back_url(self) -> str:
        """Best single back-view image: back model, else back flat."""
        return self.back_model_url or self.back_flat_url or ""

    def all_urls(self) -> list[str]:
        urls = [
            self.front_model_url,
            self.back_model_url,
            self.front_flat_url,
            self.back_flat_url,
        ]
        return [u for u in urls if u]


@dataclass(frozen=True)
class WarehouseQty:
    """Quantity available for a SKU in one SanMar warehouse."""

    warehouse_no: str
    warehouse_label: str
    quantity: int


@dataclass(frozen=True)
class InventoryRecord:
    """Per-warehouse availability + live sale pricing for one SKU (sanmar_dip)."""

    unique_key: str
    inventory_key: str
    size_index: str
    style: str
    color: str
    size: str
    warehouses: list[WarehouseQty] = field(default_factory=list)
    piece_price: Decimal | None = None
    case_price: Decimal | None = None
    each_sale_price: Decimal | None = None
    case_sale_price: Decimal | None = None
    sale_start: str = ""
    sale_end: str = ""
    discontinued_code: str = ""

    @property
    def total_qty(self) -> int:
        return sum(w.quantity for w in self.warehouses)

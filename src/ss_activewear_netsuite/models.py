"""Domain models for S&S Activewear API data.

These mirror the shape of S&S API responses but coerce to clean Python types
(Decimal for currency, ints for counts, lists for warehouse-by-warehouse
availability). All field names are snake_case regardless of how the API
delivers them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class WarehouseQty:
    """Availability at a single S&S warehouse for one SKU."""

    warehouse_abbr: str
    qty: int


@dataclass(frozen=True)
class SsProduct:
    """A single SKU (style + color + size) from S&S Activewear.

    The S&S ``sku`` is the unique identifier we use as the NetSuite external
    id (with an ``SS-`` prefix). ``style_id``/``style_name`` group SKUs into
    matrix parents; ``color_name``/``size_name`` are the matrix axes.
    """

    sku: str
    style_id: str
    style_name: str
    brand_name: str
    color_name: str
    color_code: str
    color_price_code: str
    size_name: str
    size_order: int
    gtin: str = ""
    weight: Decimal | None = None
    case_size: int | None = None
    piece_price: Decimal | None = None
    dozen_price: Decimal | None = None
    case_price: Decimal | None = None
    sale_price: Decimal | None = None
    customer_price: Decimal | None = None
    map_price: Decimal | None = None
    msrp: Decimal | None = None
    qty_available: int = 0
    warehouses: tuple[WarehouseQty, ...] = field(default_factory=tuple)
    is_closeout: bool = False
    is_discontinued: bool = False
    front_image_url: str = ""
    on_model_image_url: str = ""
    description: str = ""
    category_name: str = ""


@dataclass(frozen=True)
class SsStyle:
    """A style record: the matrix parent of one or more SKUs."""

    style_id: str
    style_name: str
    brand_name: str
    title: str = ""
    description: str = ""
    category_name: str = ""

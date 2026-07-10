"""Build NetSuite pricing payloads from SanMar SKU/inventory pricing.

SanMar exposes several prices per SKU:

* ``piece_price`` — 1–5 piece price. Mapped to the **base** price level.
* ``case_price``  — per-piece price when buying full cases. Optional second
  price level (``NETSUITE_PRICE_LEVEL_CASE``) and/or a custom field.
* ``msrp``        — suggested retail. Optional price level / custom field.
* sale prices (from ``sanmar_dip.txt``) — applied as the base price while a sale
  window is active, otherwise the regular piece price is restored.

The price body uses the REST item ``price`` sublist
(``price.items[].{priceLevel, price}``).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from ..config import NetSuiteConfig
from ..models import InventoryRecord, SkuRecord


def _money(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _price_line(level_id: str, amount: float) -> dict[str, Any]:
    return {"priceLevel": {"id": level_id}, "price": amount}


def build_price_body(sku: SkuRecord, config: NetSuiteConfig) -> dict[str, Any]:
    """Catalog-feed pricing: base = piece price, plus optional case/MSRP levels."""
    lines: list[dict[str, Any]] = []
    piece = _money(sku.piece_price)
    if piece is not None:
        lines.append(_price_line(config.price_level_base, piece))
    if config.price_level_case and sku.case_price is not None:
        lines.append(_price_line(config.price_level_case, float(sku.case_price)))
    if config.price_level_msrp and sku.msrp is not None:
        lines.append(_price_line(config.price_level_msrp, float(sku.msrp)))

    body: dict[str, Any] = {}
    if lines:
        body["price"] = {"items": lines}
    # Mirror non-base prices onto custom fields for easy reporting.
    f = config.fields
    if sku.case_price is not None:
        body[f.case_price] = float(sku.case_price)
    if sku.msrp is not None:
        body[f.msrp] = float(sku.msrp)
    if sku.map_price is not None:
        body[f.map_price] = float(sku.map_price)
    return body


def build_live_price_body(
    record: InventoryRecord, config: NetSuiteConfig, *, now: datetime | None = None
) -> dict[str, Any]:
    """Hourly-feed pricing from ``sanmar_dip.txt``: apply active sale price.

    If a sale window is active the each-sale price becomes the base price;
    otherwise the regular piece price is used.
    """
    effective = record.piece_price
    if record.each_sale_price is not None and _sale_active(record, now):
        effective = record.each_sale_price

    body: dict[str, Any] = {}
    if effective is not None:
        body["price"] = {"items": [_price_line(config.price_level_base, float(effective))]}
    if config.price_level_case and record.case_price is not None:
        body.setdefault("price", {"items": []})
        body["price"]["items"].append(
            _price_line(config.price_level_case, float(record.case_price))
        )
    return body


def _sale_active(record: InventoryRecord, now: datetime | None) -> bool:
    if not record.sale_start and not record.sale_end:
        return record.each_sale_price is not None
    now = now or datetime.now()
    start = _parse_dt(record.sale_start)
    end = _parse_dt(record.sale_end)
    if start and now < start:
        return False
    if end and now > end:
        return False
    return True


def _parse_dt(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None

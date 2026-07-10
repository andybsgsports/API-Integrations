"""Build NetSuite item payloads from SanMar styles and SKUs.

A SanMar *style* becomes a matrix **parent** item; each style/color/size SKU
becomes a matrix **child** item. The functions here emit the field bodies sent
to the SuiteTalk REST record API (and, in turn, are reused by the CSV-import
generator for the initial bulk load).

The base price is intentionally *not* set here — pricing is owned by
:mod:`sanmar_netsuite.transform.pricing` so the catalog and pricing syncs can
run on independent cadences.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..config import NetSuiteConfig
from ..models import SkuRecord, StyleRecord
from ..sanmar import constants as C


def _money(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _is_active(status: str) -> bool:
    return status.strip() in C.ACTIVE_STATUSES


def build_parent_payload(style: StyleRecord, config: NetSuiteConfig) -> dict[str, Any]:
    """Field body for the matrix parent (the style)."""
    f = config.fields
    body: dict[str, Any] = {
        "itemId": style.style,
        "displayName": style.title[:60] if style.title else style.style,
        "salesDescription": style.description,
        "purchaseDescription": style.title,
        "isInactive": style.product_status.strip().lower() == C.STATUS_DISCONTINUED.lower(),
        # Custom SanMar fields
        f.style: style.style,
        f.product_status: style.product_status,
    }
    _apply_common(body, config)
    _apply_vendor(body, config)
    # Primary image URL (front model of the first color) for quick reference.
    primary = _primary_image_url(style)
    if primary:
        body[f.front_image_url] = primary
    return body


def build_child_payload(
    sku: SkuRecord, style: StyleRecord, config: NetSuiteConfig
) -> dict[str, Any]:
    """Field body for a matrix child (one color/size SKU)."""
    f = config.fields
    name = f"{sku.style}:{sku.mainframe_color or sku.color_name}:{sku.size}"
    body: dict[str, Any] = {
        "itemId": name,
        "displayName": f"{style.title} - {sku.color_name} - {sku.size}"[:60],
        "salesDescription": sku.description or style.description,
        "isInactive": sku.is_discontinued,
        # SanMar identity / mapping fields
        f.unique_key: sku.unique_key,
        f.inventory_key: sku.inventory_key,
        f.size_index: sku.size_index,
        f.style: sku.style,
        f.mainframe_color: sku.mainframe_color,
        f.product_status: sku.product_status,
    }
    if sku.gtin:
        body[f.gtin] = sku.gtin
        body["upcCode"] = sku.gtin
    if sku.case_size is not None:
        body[f.case_size] = sku.case_size
    if sku.case_price is not None:
        body[f.case_price] = _money(sku.case_price)
    if sku.msrp is not None:
        body[f.msrp] = _money(sku.msrp)
    if sku.map_price is not None:
        body[f.map_price] = _money(sku.map_price)
    if sku.piece_weight is not None:
        body["weight"] = _money(sku.piece_weight)
        body["weightUnit"] = "lb"
    # Per-color image URL.
    color_img = style.images_by_color.get(sku.color_name)
    if color_img and color_img.primary_url():
        body[f.front_image_url] = color_img.primary_url()
    _apply_common(body, config)
    _apply_vendor(body, config)
    return body


def _apply_common(body: dict[str, Any], config: NetSuiteConfig) -> None:
    """Attach account/subsidiary references that NetSuite requires on new items."""
    if config.subsidiary_id:
        body.setdefault("subsidiary", {"items": [{"id": config.subsidiary_id}]})
    if config.income_account_id:
        body.setdefault("incomeAccount", {"id": config.income_account_id})
    if config.asset_account_id:
        body.setdefault("assetAccount", {"id": config.asset_account_id})
    if config.cogs_account_id:
        body.setdefault("cogsAccount", {"id": config.cogs_account_id})


def _apply_vendor(body: dict[str, Any], config: NetSuiteConfig) -> None:
    if config.sanmar_vendor_id:
        body.setdefault("vendor", {"id": config.sanmar_vendor_id})


def _primary_image_url(style: StyleRecord) -> str:
    for color in style.colors:
        img = style.images_by_color.get(color)
        if img and img.primary_url():
            return img.primary_url()
    return ""

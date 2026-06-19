"""Build NetSuite item payloads from S&S Activewear products.

Mirrors :mod:`sanmar_netsuite.transform.catalog` but uses S&S field names and
the ``custitem_ss_*`` field map. External ids are prefixed ``SS-`` so they
don't collide with SanMar items in the same NetSuite account.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..config import SsAppConfig
from ..models import SsProduct

SS_EXTERNAL_ID_PREFIX = "SS-"
SS_STYLE_EXTERNAL_ID_PREFIX = "SS-STYLE-"


def sku_external_id(product: SsProduct) -> str:
    return f"{SS_EXTERNAL_ID_PREFIX}{product.sku}"


def style_external_id(product: SsProduct) -> str:
    return f"{SS_STYLE_EXTERNAL_ID_PREFIX}{product.style_id}"


def _money(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _display_name(product: SsProduct) -> str:
    parts = [product.style_name or product.style_id, product.color_name, product.size_name]
    return " - ".join(p for p in parts if p)


def warehouses_to_payload(product: SsProduct) -> str:
    """Serialise per-warehouse availability as ``IL:600;TX:200;...``.

    NetSuite Long Text fields are well-suited to this format — compact,
    grep-able, and survives copy/paste between records.
    """
    if not product.warehouses:
        return ""
    return ";".join(f"{w.warehouse_abbr}:{w.qty}" for w in product.warehouses)


def build_item_payload(product: SsProduct, config: SsAppConfig) -> dict[str, Any]:
    """Build the body of a NetSuite ``inventoryItem`` upsert for one SKU."""

    f = config.netsuite_fields
    ns = config.netsuite
    body: dict[str, Any] = {
        "itemId": f"{product.style_name}:{product.color_name}:{product.size_name}",
        "displayName": _display_name(product),
        "isInactive": product.is_discontinued,
        f.sku: product.sku,
        f.style_id: product.style_id,
        f.style_name: product.style_name,
        f.color_name: product.color_name,
        f.color_code: product.color_code,
        f.size_name: product.size_name,
        f.size_order: product.size_order,
        f.brand: product.brand_name,
        f.gtin: product.gtin,
        f.is_closeout: bool(product.is_closeout),
        f.is_discontinued: bool(product.is_discontinued),
        f.qty_available: product.qty_available,
        f.qty_by_whse: warehouses_to_payload(product),
        f.front_image_url: product.front_image_url,
        f.on_model_image_url: product.on_model_image_url,
    }
    if product.weight is not None:
        body[f.weight] = float(product.weight)
        body["weight"] = float(product.weight)
    if product.case_size is not None:
        body[f.case_size] = product.case_size
    if product.piece_price is not None:
        body[f.piece_price] = _money(product.piece_price)
    if product.dozen_price is not None:
        body[f.dozen_price] = _money(product.dozen_price)
    if product.case_price is not None:
        body[f.case_price] = _money(product.case_price)
    if product.map_price is not None:
        body[f.map_price] = _money(product.map_price)
    if product.msrp is not None:
        body[f.msrp] = _money(product.msrp)

    # Pricing matrix entry — base price level.
    base_price = product.sale_price or product.customer_price or product.piece_price
    if base_price is not None:
        body["price"] = [
            {
                "priceLevel": {"id": ns.price_level_base},
                "price": [{"value": float(base_price)}],
            }
        ]

    # GL accounts on new items.
    if ns.income_account_id:
        body["incomeAccount"] = {"id": ns.income_account_id}
    if ns.asset_account_id:
        body["assetAccount"] = {"id": ns.asset_account_id}
    if ns.cogs_account_id:
        body["cogsAccount"] = {"id": ns.cogs_account_id}
    if ns.subsidiary_id:
        body["subsidiary"] = [{"id": ns.subsidiary_id}]
    if config.ss_vendor_id:
        body["vendor"] = {"id": config.ss_vendor_id}

    return body

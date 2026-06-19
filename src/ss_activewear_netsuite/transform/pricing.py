"""Map S&S product pricing onto NetSuite price levels + custom fields."""

from __future__ import annotations

from typing import Any

from ..config import SsAppConfig
from ..models import SsProduct


def build_pricing_payload(product: SsProduct, config: SsAppConfig) -> dict[str, Any]:
    """Compose a NetSuite ``inventoryItem`` PATCH body carrying just pricing.

    Base price level receives the sale_price (if any) else customer_price
    else piece_price. Other pricing variants (dozen, case, MAP, MSRP) land
    on ``custitem_ss_*`` fields. Optional price levels for case/MSRP can be
    configured via env to materialise those as real NetSuite price levels.
    """

    f = config.netsuite_fields
    ns = config.netsuite
    body: dict[str, Any] = {}

    base = product.sale_price or product.customer_price or product.piece_price
    if base is not None:
        body.setdefault("price", []).append(
            {
                "priceLevel": {"id": ns.price_level_base},
                "price": [{"value": float(base)}],
            }
        )
    if ns.price_level_case and product.case_price is not None:
        body.setdefault("price", []).append(
            {
                "priceLevel": {"id": ns.price_level_case},
                "price": [{"value": float(product.case_price)}],
            }
        )
    if ns.price_level_msrp and product.msrp is not None:
        body.setdefault("price", []).append(
            {
                "priceLevel": {"id": ns.price_level_msrp},
                "price": [{"value": float(product.msrp)}],
            }
        )
    if product.piece_price is not None:
        body[f.piece_price] = float(product.piece_price)
    if product.dozen_price is not None:
        body[f.dozen_price] = float(product.dozen_price)
    if product.case_price is not None:
        body[f.case_price] = float(product.case_price)
    if product.map_price is not None:
        body[f.map_price] = float(product.map_price)
    if product.msrp is not None:
        body[f.msrp] = float(product.msrp)

    return body

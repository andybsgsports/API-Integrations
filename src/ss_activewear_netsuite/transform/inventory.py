"""Map S&S availability onto NetSuite custom fields.

Same architectural call as SanMar: S&S ``qty`` is *S&S's* warehouse stock,
not yours. Writing it to ``quantityOnHand`` would misstate inventory asset
value. We route it to ``custitem_ss_qty_available`` (total) and
``custitem_ss_qty_by_whse`` (per-warehouse, semicolon-delimited).
"""

from __future__ import annotations

from typing import Any

from ..config import SsAppConfig
from ..models import SsProduct
from .catalog import warehouses_to_payload


def build_inventory_payload(product: SsProduct, config: SsAppConfig) -> dict[str, Any]:
    f = config.netsuite_fields
    return {
        f.qty_available: product.qty_available,
        f.qty_by_whse: warehouses_to_payload(product),
    }

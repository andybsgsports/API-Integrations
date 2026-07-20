"""Map S&S availability onto NetSuite custom fields.

Same architectural call as SanMar: S&S ``qty`` is *S&S's* warehouse stock,
not yours. Writing it to ``quantityOnHand`` would misstate inventory asset
value. We route the total to ``custitem_ss_qty_available``; the per-warehouse
breakdown now lives in the dedicated per-warehouse integer fields (see
``scripts/warehouse_fields.py``), so the old ``custitem_ss_qty_by_whse`` text
blob was retired.
"""

from __future__ import annotations

from typing import Any

from ..config import SsAppConfig
from ..models import SsProduct


def build_inventory_payload(product: SsProduct, config: SsAppConfig) -> dict[str, Any]:
    f = config.netsuite_fields
    return {
        f.qty_available: product.qty_available,
    }

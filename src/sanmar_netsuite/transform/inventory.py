"""Build NetSuite availability payloads from SanMar inventory.

Important modeling note: SanMar's warehouse quantities reflect *SanMar's* stock,
not yours. You generally must NOT write these to NetSuite's real
``quantityOnHand`` — that is owned by inventory transactions (receipts,
adjustments) and represents inventory you actually hold. Doing so would corrupt
your books.

Instead we store SanMar availability on **custom item fields** for visibility
and downstream automation (e.g. drop-ship availability checks, reorder logic):

* ``custitem_sanmar_qty_available`` — total across all SanMar warehouses.
* ``custitem_sanmar_qty_by_whse``   — JSON/text breakdown per warehouse.

If you later decide to mirror SanMar stock into NetSuite locations, do it via
Inventory Adjustment transactions, not item-field writes (out of scope here and
deliberately not automated).
"""

from __future__ import annotations

import json
from typing import Any

from ..config import NetSuiteConfig
from ..models import InventoryRecord


def build_availability_body(record: InventoryRecord, config: NetSuiteConfig) -> dict[str, Any]:
    f = config.fields
    breakdown = {w.warehouse_no: w.quantity for w in record.warehouses}
    body: dict[str, Any] = {
        f.qty_available: record.total_qty,
        f.qty_by_whse: json.dumps(breakdown, separators=(",", ":")),
    }
    return body


def availability_summary(record: InventoryRecord) -> str:
    """Human-readable one-liner for logs."""
    parts = [f"{w.warehouse_label}={w.quantity}" for w in record.warehouses]
    return f"{record.unique_key} total={record.total_qty} [{', '.join(parts)}]"

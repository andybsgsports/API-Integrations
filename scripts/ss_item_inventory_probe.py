"""For one NetSuite item, show what S&S's live /Inventory returns per warehouse.

Reads the item's S&S SKU (custitem_ss_sku) and GTIN from NetSuite, then calls
the S&S /Inventory/{sku} endpoint and prints each warehouse's qty alongside
the value currently stored on the item. This is the decisive check for an
"inventory not matching" report: it shows, side by side, (a) what NetSuite
stores now, and (b) what the S&S API returns for this exact SKU right now.

Env: ITEM_ID (default 84084 = 8000-Black-Medium). Read-only.
"""

from __future__ import annotations

import os

from warehouse_fields import SS_WHSE_FIELDS

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from ss_activewear_netsuite.config import get_config as ss_config
from ss_activewear_netsuite.ss_activewear.client import SsClient


def main() -> int:
    item_id = os.environ.get("ITEM_ID") or "84084"
    ns = NetSuiteClient(get_config().netsuite)

    cols = ["itemid", "custitem_ss_sku", "custitem_ss_gtin", "upccode",
            "custitem_ss_qty_available"] + [sid for sid, _ in SS_WHSE_FIELDS.values()]
    rows = ns.suiteql(
        f"SELECT {', '.join(cols)} FROM item WHERE id = {int(item_id)}"
    )
    if not rows:
        print(f"item {item_id} not found")
        return 1
    it = rows[0]
    sku = str(it.get("custitem_ss_sku") or "").strip()
    print(f"=== NetSuite item {item_id} ===")
    print(f"  itemid            {it.get('itemid')!r}")
    print(f"  custitem_ss_sku   {sku!r}")
    print(f"  gtin/upc          {it.get('custitem_ss_gtin') or it.get('upccode')!r}")
    print(f"  qty_available     {it.get('custitem_ss_qty_available')!r}")
    print("  stored per-warehouse:")
    for abbr, (fid, label) in SS_WHSE_FIELDS.items():
        print(f"    {abbr:<4} ({label:<28}) {it.get(fid.lower()) or it.get(fid)!r}")

    if not sku:
        print("\nno S&S SKU on this item -- nothing to compare against the API")
        return 0

    ss = SsClient(ss_config().ss_api)
    print(f"\n=== S&S live /Inventory/{sku} ===")
    try:
        inv = ss.get_inventory(sku)
    except Exception as exc:  # noqa: BLE001 - probe, surface any API error
        print(f"  /Inventory call failed: {str(exc)[:200]}")
        return 1
    if inv is None:
        print("  /Inventory returned no record for this SKU")
        return 0
    total = 0
    for w in inv.warehouses:
        total += int(w.qty or 0)
        print(f"    {w.warehouse_abbr:<4} qty={int(w.qty or 0):,}")
    print(f"  API warehouse total: {total:,}")
    print(f"  API aggregate qty:   {getattr(inv, 'qty', '?')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Read-only: show how NetSuite stores the vendor lines on a few items.

Why: the vendor line price sync writes ``purchasePrice`` on the preferred
vendor line, and on some items (94 Under Armour items in the sandbox,
2026-11-20) the value does not change. This prints the raw ``itemVendor``
sublist, with any nested price lines, so the right field to write is known.

ITEM_PROBE = comma list of internal ids or item names. Writes nothing.
"""

from __future__ import annotations

import json
import os

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient


def main() -> int:
    wanted = [w.strip() for w in os.environ.get("ITEM_PROBE", "").split(",") if w.strip()]
    if not wanted:
        print("ITEM_PROBE is empty; nothing to do")
        return 0
    client = NetSuiteClient(get_config().netsuite)
    for want in wanted:
        if want.isdigit():
            rid = want
        else:
            safe = want.replace("'", "''")
            rows = client.suiteql(f"SELECT id FROM item WHERE itemid = '{safe}'")
            if not rows:
                print(f"\n== {want}: not found")
                continue
            rid = str(rows[0]["id"])
        print(f"\n== item {want} (id {rid})")
        for r in client.suiteql(
            "SELECT i.itemid, i.cost, i.matrixtype, iv.vendor, iv.preferredvendor, "
            "iv.purchaseprice, iv.vendorcode FROM itemvendor iv "
            f"JOIN item i ON i.id = iv.item WHERE iv.item = {rid}"
        ):
            print(f"  suiteql: {json.dumps(r, default=str)}")
        try:
            rows = client.suiteql(
                f"SELECT * FROM itemvendorprice WHERE item = {rid}")
            for r in rows:
                print(f"  itemvendorprice: {json.dumps(r, default=str)}")
            if not rows:
                print("  itemvendorprice: no rows")
        except Exception as exc:  # noqa: BLE001 - table may not be exposed
            print(f"  itemvendorprice: not readable ({str(exc)[:160]})")
        url = (f"{client._config.rest_base}{client.RECORD_PATH}/inventoryItem/{rid}"
               "/itemVendor?expandSubResources=true")
        try:
            resp = client._request("GET", url)
            print(f"  REST itemVendor ({resp.status_code}): "
                  f"{json.dumps(resp.json(), default=str)[:3000]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  REST itemVendor: failed ({str(exc)[:300]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

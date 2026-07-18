"""Dump selected fields for one item (read-only) -- warehouse cols + brand.

Confirms whether the per-warehouse fields (custitem_ss_qty_pa/cc/...) and the
native manufacturer field actually carry values on a matched S&S item, so we
can tell a form-layout (not displayed) issue from a data (not written) issue.

Env: ITEM_DUMP_ID (default 84083).
"""

from __future__ import annotations

import os

from warehouse_fields import SANMAR_QTY_FIELDS, SS_QTY_FIELDS

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient


def main() -> int:
    item_id = os.environ.get("ITEM_DUMP_ID", "84083").strip()
    client = NetSuiteClient(get_config().netsuite)
    base = ["itemid", "manufacturer", "custitem_ss_brand", "custitem_ss_qty_available",
            "custitem_ss_qty_by_whse"]
    cols = base + SS_QTY_FIELDS + SANMAR_QTY_FIELDS
    # probe each column's existence individually so one missing field doesn't
    # blank the whole row
    print(f"=== item {item_id} field dump ===")
    for c in cols:
        try:
            r = client.suiteql(f"SELECT {c} AS v FROM item WHERE id = {int(item_id)}")
            val = r[0].get("v") if r else None
            print(f"  {c:<34} {val!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {c:<34} <<< FIELD MISSING/ERROR: {str(exc)[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

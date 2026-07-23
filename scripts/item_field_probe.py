"""Read-only field dump for a handful of items, to explain data differences.

Given a comma list of item names (ITEM_PROBE), print the fields that reveal
WHICH feed populated each -- display name, descriptions, manufacturer, and the
per-vendor key fields (S&S / Momentec) plus the feed-source stamp. Useful when
two children of the same style show different info (e.g. one named from the S&S
feed, the rest from Momentec). No writes.
"""

from __future__ import annotations

import os

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape

DEFAULT = ("695HBM-J.Navy-2X-Large,695HBM-J.Navy-3X-Large,695HBM-J.Navy-Large")

FIELDS = [
    "id", "itemid", "displayname", "salesdescription", "purchasedescription",
    "manufacturer", "custitem_ss_sku", "custitem_ss_style", "custitem_ss_brand",
    "custitem_mtec_item_sku", "custitem_mtec_style", "custitem_feed_source",
]


def main() -> int:
    names = [n.strip() for n in (os.environ.get("ITEM_PROBE") or DEFAULT).split(",")
             if n.strip()]
    client = NetSuiteClient(get_config().netsuite)
    in_list = ", ".join(f"'{_sql_escape(n)}'" for n in names)
    # Keep only columns that actually project (a bad one 400s the whole query).
    cols = ["id", "itemid"]
    for f in FIELDS:
        if f in cols:
            continue
        try:
            client.suiteql(f"SELECT {f} FROM item WHERE rownum <= 1")
            cols.append(f)
        except Exception:  # noqa: BLE001 - column absent/unqueryable; skip
            pass
    rows = client.suiteql(
        f"SELECT {', '.join(cols)} FROM item WHERE itemid IN ({in_list})"
    )
    by_name = {str(r.get("itemid")): r for r in rows}
    for n in names:
        r = by_name.get(n)
        print(f"\n=== {n} ===")
        if not r:
            print("  (not found)")
            continue
        for f in FIELDS:
            v = r.get(f)
            if v not in (None, ""):
                print(f"  {f}: {v!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Read-only: compare a parent matrix item to its children across fields that
should be shared (not color/size-specific), to see what's actually out of
sync before building a parent/child sync writer.
"""

from __future__ import annotations

import os

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape

STYLE = os.environ.get("PARENT_PROBE_STYLE", "105100")
FIELDS = [
    "displayname",
    "description",
    "purchasedescription",
    "vendorname",
    "department",
    "class",
    "subsidiary",
    "custitem_atlas_item_image",
    "parent",
]


def main() -> int:
    client = NetSuiteClient(get_config().netsuite)
    cols = ", ".join(FIELDS)
    parent_rows = client.suiteql(
        f"SELECT id, itemid, {cols} FROM item WHERE itemid = '{_sql_escape(STYLE)}'"
    )
    if not parent_rows:
        print(f"no item found with itemid = {STYLE!r}")
        return 1
    parent = parent_rows[0]
    print(f"PARENT {parent['itemid']} (id {parent['id']}):")
    for f in FIELDS:
        print(f"  {f}: {parent.get(f)!r}")

    children = client.suiteql(
        f"SELECT id, itemid, {cols} FROM item WHERE parent = {int(parent['id'])}"
    )
    print(f"\n{len(children)} children:")
    for c in children[:6]:
        print(f"  {c['itemid']} (id {c['id']}):")
        for f in FIELDS:
            print(f"    {f}: {c.get(f)!r}")

    print("\n=== how many items total have a non-null 'parent' (i.e. are children)? ===")
    n = client.suiteql("SELECT COUNT(*) AS n FROM item WHERE parent IS NOT NULL")
    print(f"  {n[0]['n']}")
    print("=== how many distinct parent ids are referenced? ===")
    n2 = client.suiteql(
        "SELECT COUNT(DISTINCT parent) AS n FROM item WHERE parent IS NOT NULL"
    )
    print(f"  {n2[0]['n']}")

    print("\n=== mismatch scan: distinct parents with >=1 child disagreeing, per field ===")
    for f in FIELDS:
        if f == "parent":
            continue
        try:
            rows = client.suiteql(
                "SELECT COUNT(DISTINCT p.id) AS n FROM item p "
                "JOIN item c ON c.parent = p.id "
                f"WHERE p.parent IS NULL AND NVL(TO_CHAR(p.{f}),'') <> NVL(TO_CHAR(c.{f}),'')"
            )
            print(f"  {f}: {rows[0]['n']}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {f}: query failed: {str(exc)[:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

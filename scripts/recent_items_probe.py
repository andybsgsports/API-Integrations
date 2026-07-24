"""Read-only probe: items created in NetSuite in the last N hours.

Verifies whether a CSV-import batch actually created records -- the import
TASK finishing COMPLETE says nothing about per-row rejects, and the row-level
error file is only visible on the UI's Import Job Status page. Prints id,
name, external id, and created timestamp. Never writes; always exits 0.

Env: RECENT_HOURS (default 3).
"""

from __future__ import annotations

import os

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient


def main() -> int:
    hours = int(os.environ.get("RECENT_HOURS", "3") or "3")
    client = NetSuiteClient(get_config().netsuite)
    for created_col in ("createddate", "created"):
        try:
            rows = client.suiteql(
                f"SELECT id, itemid, externalid, {created_col} FROM item "
                f"WHERE {created_col} >= SYSDATE - {hours}/24 "
                f"ORDER BY {created_col} DESC"
            )
        except Exception as exc:  # noqa: BLE001 - column name varies by account
            print(f"({created_col} query failed: {str(exc)[:120]})")
            continue
        print(f"items created in the last {hours}h (via {created_col}): {len(rows)}")
        for r in rows[:60]:
            print(f"  id={r.get('id')} itemid={r.get('itemid')!r} "
                  f"ext={r.get('externalid')!r} created={r.get(created_col)}")
        if len(rows) > 60:
            print(f"  ... +{len(rows) - 60} more")
        return 0
    print("could not query a created-date column at all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

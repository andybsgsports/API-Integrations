"""Set Warehouse (``location``) + Preferred Location (``preferredLocation``) =
Badger Sporting Goods on every matrix item (parent and child).

The account has a single location, and Preferred Location drives sales-order
auto-population + web shipping-cost calc, so every item should carry Badger
Sporting Goods in both. The create-import + child-finalize set ``location`` on
children but never Preferred Location, and never touched parents -- so this
fills both gaps catalog-wide.

Diff-aware (writes only where a value is missing/wrong), chunked by internal-id
range to stay under SuiteQL's 100k-row window, dry-run by default
(``SYNC_DRY_RUN``). ``UPDATE_MAX_ITEMS`` caps writes; ``LOCATION_FIX_CHUNK`` the
id-range width.
"""

from __future__ import annotations

import os

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape

LOCATION_NAME = "Badger Sporting Goods"


def location_body(row: dict, loc_id: str, has_pref: bool) -> dict:
    """Fields to set on one item: Warehouse + Preferred Location, where they
    aren't already Badger Sporting Goods."""
    body: dict[str, object] = {}
    if str(row.get("location") or "").strip() != loc_id:
        body["location"] = {"id": loc_id}
    # If preferredlocation doesn't project we can't diff -- set it regardless so
    # it still gets populated.
    if not has_pref or str(row.get("preferredlocation") or "").strip() != loc_id:
        body["preferredLocation"] = {"id": loc_id}
    return body


def _projects(client: NetSuiteClient, col: str) -> bool:
    try:
        client.suiteql(f"SELECT {col} FROM item WHERE rownum <= 1")
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    chunk = int(os.environ.get("LOCATION_FIX_CHUNK", "40000") or "40000")
    client = NetSuiteClient(cfg.netsuite)

    rows = client.suiteql(f"SELECT id FROM location WHERE name = '{_sql_escape(LOCATION_NAME)}'")
    if not rows:
        print(f"location {LOCATION_NAME!r} not found -- aborting")
        return 1
    loc_id = str(rows[0]["id"])
    print(f"location {LOCATION_NAME!r} -> internal id {loc_id}")

    has_pref = _projects(client, "preferredlocation")
    if not has_pref:
        print("  (preferredlocation column does not project; setting it unconditionally)")
    cols = "id, location" + (", preferredlocation" if has_pref else "")

    max_rows = client.suiteql(
        "SELECT MAX(id) AS m FROM item WHERE matrixtype IN ('PARENT', 'CHILD')"
    )
    max_id = int(max_rows[0]["m"]) if max_rows and max_rows[0].get("m") else 0
    print(f"scanning matrix items up to id {max_id} in chunks of {chunk}")

    considered = written = failures = scanned = 0
    samples = 0
    for lo in range(0, max_id + 1, chunk):
        hi = lo + chunk - 1
        items = client.suiteql(
            f"SELECT {cols} FROM item "
            f"WHERE matrixtype IN ('PARENT', 'CHILD') AND id BETWEEN {lo} AND {hi}"
        )
        for r in items:
            scanned += 1
            body = location_body(r, loc_id, has_pref)
            if not body:
                continue
            if max_items and considered >= max_items:
                continue
            considered += 1
            if samples < 8:
                samples += 1
                print(f"  item {r.get('id')}: set {list(body)}")
            if not allow_write:
                written += 1
                continue
            try:
                client.update_record("inventoryItem", str(r["id"]), body)
                written += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if failures <= 10:
                    print(f"  FAILED item {r.get('id')}: {str(exc)[:120]} "
                          f":: {str(getattr(exc, 'payload', ''))[:200]}")

    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(f"\nlocation fix: {verb} {written} item(s); scanned: {scanned:,}; "
          f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Set Display in Web Site (isOnline) = Yes on every item that has a real
picture (custitem_atlas_item_image is non-blank).

Field confirmed via the REST metadata catalog: ``isOnline`` / SuiteQL
``isonline`` -- "Display in Website... Check this box to make this item
available online in your Web site."

One-directional: only turns isOnline ON when an image is present and it's
currently off. Items without an image are left alone rather than forced to
No -- we only know about custitem_atlas_item_image, and flipping web
visibility OFF on an item that might be correctly published through some
other path would be a destructive guess.

Diff-aware, id-range-chunked, dry-run by default. ``UPDATE_MAX_ITEMS`` caps
writes; uses the shared concurrent_writes helper.
"""

from __future__ import annotations

import os

from concurrent_writes import write_records

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

IMAGE_FIELD = "custitem_atlas_item_image"


def _projects(client: NetSuiteClient, col: str) -> bool:
    try:
        client.suiteql(f"SELECT {col} FROM item WHERE rownum <= 1")
        return True
    except Exception:  # noqa: BLE001
        return False


def plan_body(row: dict) -> dict:
    """Set isOnline=True when the item has an image and isn't already online."""
    has_image = bool(str(row.get(IMAGE_FIELD) or "").strip())
    is_online = str(row.get("isonline") or "").strip().upper() in ("T", "TRUE", "1")
    if has_image and not is_online:
        return {"isOnline": True}
    return {}


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    chunk = int(os.environ.get("WEB_DISPLAY_FIX_CHUNK", "40000") or "40000")
    client = NetSuiteClient(cfg.netsuite)

    if not _projects(client, "isonline"):
        print("isonline does not project -- cannot proceed")
        return 1
    if not _projects(client, IMAGE_FIELD):
        print(f"{IMAGE_FIELD} does not project -- cannot proceed")
        return 1

    max_rows = client.suiteql(
        "SELECT MAX(id) AS m FROM item WHERE matrixtype IN ('PARENT', 'CHILD')"
    )
    max_id = int(max_rows[0]["m"]) if max_rows and max_rows[0].get("m") else 0
    print(f"scanning matrix items up to id {max_id} in chunks of {chunk}")

    considered = written = failures = scanned = with_image = 0
    samples = 0
    write_jobs: list[tuple[str, dict]] = []
    for lo in range(0, max_id + 1, chunk):
        hi = lo + chunk - 1
        items = client.suiteql(
            f"SELECT id, isonline, {IMAGE_FIELD} FROM item "
            f"WHERE matrixtype IN ('PARENT', 'CHILD') AND id BETWEEN {lo} AND {hi}"
        )
        for r in items:
            scanned += 1
            if str(r.get(IMAGE_FIELD) or "").strip():
                with_image += 1
            body = plan_body(r)
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
            write_jobs.append((str(r["id"]), body))

    def _on_err(rid: str, exc: Exception) -> None:
        nonlocal failures
        failures += 1
        if failures <= 10:
            detail = getattr(exc, "payload", "")
            print(f"  FAILED item {rid}: {str(exc)[:150]} :: {str(detail)[:300]}")

    if allow_write and write_jobs:
        w, f = write_records(client, "inventoryItem", write_jobs, on_error=_on_err)
        written += w
        failures += f

    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(f"\nweb display fix: {verb} {written} item(s); scanned: {scanned:,}; "
          f"with an image: {with_image:,}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

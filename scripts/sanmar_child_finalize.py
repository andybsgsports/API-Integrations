"""Finalize newly-created SanMar matrix children by copying their parent's
structural fields down: Department, Class, and the Purchase / Sales Description.

The CSV create-import can't reliably set list-reference fields on a matrix child,
so children created that way land with Department / Class / descriptions blank
even though the parent carries the correct values. Rather than re-derive them
from the feed (and fight name->id resolution in the importer), this reads each
child's PARENT -- the record BSG already curates -- and copies its values
straight down. The parent's reference columns come back from SuiteQL as internal
ids, so the copy is exact (no lookup).

Rules that keep it safe:
* **Fill blanks only.** A field is written only when the parent has a value and
  the child is blank -- it never overwrites data a child already has.
* **SanMar children only** (``externalid LIKE 'SANMAR%'``), so no other vendor's
  items are touched.
* Parent-chunked scan (SuiteQL caps a result window at 100k rows; a flat
  ``parent IS NOT NULL`` scan silently truncates this 150k+ catalog).
* Dry-run by default (``SYNC_DRY_RUN``); ``UPDATE_MAX_ITEMS`` caps writes.

Units of measure and costing method are intentionally NOT handled here: on a
matrix item those are defined on the parent and locked at create, so they can't
be PATCHed onto an existing child. Income account + Base Price are handled
separately by ``reconcile-items``.
"""

from __future__ import annotations

import os

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

# (SuiteQL column, REST field, is a List/Record reference). Reference columns
# come back from SuiteQL as the internal id, so the copy is {"id": <that id>}.
# These four are exactly the item fields parent_sync.py already writes, i.e.
# proven patchable over REST.
COPY_FIELDS: list[tuple[str, str, bool]] = [
    ("department", "department", True),
    ("class", "class", True),
    ("salesdescription", "salesDescription", False),
    ("purchasedescription", "purchaseDescription", False),
]


def plan_body(child: dict, parent: dict) -> dict:
    """The REST body to copy parent->child: parent has a value, child is blank."""
    body: dict[str, object] = {}
    for col, rest, is_ref in COPY_FIELDS:
        pval = str(parent.get(col) or "").strip()
        cval = str(child.get(col) or "").strip()
        if pval and not cval:
            body[rest] = {"id": pval} if is_ref else pval
    return body


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    client = NetSuiteClient(cfg.netsuite)

    cols = ", ".join(c for c, _, _ in COPY_FIELDS)
    parents = client.suiteql(f"SELECT id, itemid, {cols} FROM item WHERE parent IS NULL")
    parents_by_id = {str(p["id"]): p for p in parents}
    print(f"candidate parent items: {len(parents):,}")

    parent_ids = list(parents_by_id)
    considered = written = unchanged = failures = scanned = 0
    samples = 0
    for i in range(0, len(parent_ids), 250):
        chunk = ", ".join(parent_ids[i : i + 250])
        children = client.suiteql(
            f"SELECT id, parent, itemid, {cols} FROM item "
            f"WHERE parent IN ({chunk}) AND externalid LIKE 'SANMAR%'"
        )
        for child in children:
            scanned += 1
            parent = parents_by_id.get(str(child.get("parent") or ""))
            if not parent:
                continue
            body = plan_body(child, parent)
            if not body:
                unchanged += 1
                continue
            if max_items and considered >= max_items:
                continue
            considered += 1
            if samples < 12:
                samples += 1
                fields = ", ".join(body)
                print(f"  {child.get('itemid')} (id {child.get('id')}) <- parent "
                      f"{parent.get('itemid')}: set {fields}")
            if not allow_write:
                written += 1
                continue
            try:
                client.update_record("inventoryItem", str(child["id"]), body)
                written += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if failures <= 10:
                    detail = getattr(exc, "payload", "")
                    print(f"  FAILED child {child.get('id')}: {str(exc)[:120]} "
                          f":: {str(detail)[:250]}")

    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(
        f"\nchild finalize: {verb} {written} child(ren); SanMar children scanned: "
        f"{scanned:,}; already complete: {unchanged:,}; failures: {failures}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

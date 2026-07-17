"""Sync matrix parent items to mirror their children for fields that aren't
color/size-specific: display name, descriptions, vendor, department, class,
and the real product image. Runs on CI.

Matrix parents (style-level records like ``105100``) never got the
plain-title update or the image backfill, because both writers join items by
a supplier SKU key field that only lives on children -- parents don't carry
one. This reads children directly via NetSuite's own ``parent`` link instead
of re-deriving anything from the supplier feeds.

For each parent, takes the *mode* (most common non-empty value) among its
children per field, so a lone stale/unmatched child can't skew the parent's
value. Diff-aware: only fields that actually differ from the parent's
current value are written. Honors SYNC_DRY_RUN; UPDATE_MAX_ITEMS caps writes.
"""

from __future__ import annotations

import os
from collections import Counter

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

# SuiteQL column -> (REST field name, is a List/Record reference field)
FIELDS = {
    "displayname": ("displayName", False),
    "description": ("salesDescription", False),
    "purchasedescription": ("purchaseDescription", False),
    "vendorname": ("vendorName", False),
    "department": ("department", True),
    "class": ("class", True),
    "custitem_atlas_item_image": ("custitem_atlas_item_image", False),
}


def _mode(values: list[str]) -> str | None:
    non_empty = [v for v in values if v]
    if not non_empty:
        return None
    return Counter(non_empty).most_common(1)[0][0]


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    client = NetSuiteClient(cfg.netsuite)

    cols = ", ".join(FIELDS)
    parents = client.suiteql(f"SELECT id, itemid, {cols} FROM item WHERE parent IS NULL")
    parents_by_id = {str(p["id"]): p for p in parents}
    print(f"candidate parent items: {len(parents):,}")

    # SuiteQL caps a single query's result window at 100,000 rows; a plain
    # "parent IS NOT NULL" scan silently truncates well before covering all
    # children (151k+ in this catalog). Chunk by parent id instead so every
    # query stays well under that cap.
    parent_ids = list(parents_by_id)
    values_by_parent: dict[str, dict[str, list[str]]] = {}
    total_children = 0
    for i in range(0, len(parent_ids), 250):
        chunk = parent_ids[i : i + 250]
        in_list = ", ".join(chunk)
        rows = client.suiteql(f"SELECT id, parent, {cols} FROM item WHERE parent IN ({in_list})")
        total_children += len(rows)
        for c in rows:
            pid = str(c.get("parent") or "")
            bucket = values_by_parent.setdefault(pid, {f: [] for f in FIELDS})
            for f in FIELDS:
                v = c.get(f)
                if v is not None and str(v).strip() != "":
                    bucket[f].append(str(v))
    print(f"children scanned: {total_children:,}")

    considered = written = unchanged = no_children = failures = 0
    samples = 0
    for pid, parent in parents_by_id.items():
        bucket = values_by_parent.get(pid)
        if not bucket:
            no_children += 1
            continue
        body: dict[str, object] = {}
        diffs: dict[str, tuple[str, str]] = {}
        for f, (rest_name, is_ref) in FIELDS.items():
            mode_val = _mode(bucket[f])
            if mode_val is None:
                continue
            current = str(parent.get(f) or "")
            if current == mode_val:
                continue
            body[rest_name] = {"id": mode_val} if is_ref else mode_val
            diffs[f] = (current, mode_val)
        if not body:
            unchanged += 1
            continue
        if max_items and considered >= max_items:
            continue
        considered += 1
        if samples < 10:
            samples += 1
            print(f"  sample parent {pid} ({parent.get('itemid')}):")
            for f, (old, new) in diffs.items():
                print(f"    {f}: {old!r} -> {new!r}")
        if not allow_write:
            written += 1
            continue
        try:
            client.update_record("inventoryItem", pid, body)
            written += 1
        except Exception as exc:  # noqa: BLE001
            failures += 1
            if failures <= 10:
                detail = getattr(exc, "payload", "")
                print(f"  FAILED parent {pid}: {str(exc)[:120]} :: {str(detail)[:250]}")

    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(
        f"\nparent sync: {verb} {written} parent(s); unchanged: {unchanged}; "
        f"no children: {no_children}; failures: {failures}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

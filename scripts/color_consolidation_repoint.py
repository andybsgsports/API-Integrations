"""Repoint items off duplicate color-list values onto their canonical value (CI).

Consumes ``data/color_consolidation_plan.csv`` (built by
``color_consolidation_plan.py``): one row per duplicate value to retire,
with the canonical id it should merge into and how many items currently
point at it. This script re-derives the actual item ids at run time
(the plan only carries counts) and PATCHes each item's ``custitem_bsg_color``
from the retired value to the canonical one.

Retired list values themselves are left in place -- once nothing points at
them they're inert, and deleting customlist values isn't this script's job.
Honors ``SYNC_DRY_RUN``; ``UPDATE_MAX_ITEMS`` caps writes.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.adopt import COLOR_FIELD
from sanmar_netsuite.netsuite.client import NetSuiteClient

ROOT = Path(__file__).resolve().parents[1]


def load_plan() -> dict[str, str]:
    """retire_id -> canonical_id, only rows with items to move."""
    out: dict[str, str] = {}
    with (ROOT / "data" / "color_consolidation_plan.csv").open(
        encoding="utf-8", newline=""
    ) as fh:
        for r in csv.DictReader(fh):
            if int(r["items_to_repoint"] or 0) > 0:
                out[r["retire_id"]] = r["canonical_id"]
    return out


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    client = NetSuiteClient(cfg.netsuite)

    plan = load_plan()
    print(f"retired values with items to repoint: {len(plan):,}")
    if not plan:
        print("nothing to do")
        return 0

    retire_ids = sorted(plan)
    considered = written = failures = 0
    samples = 0
    for i in range(0, len(retire_ids), 100):
        chunk = retire_ids[i : i + 100]
        in_list = ", ".join(chunk)
        query = f"SELECT id, {COLOR_FIELD} AS c FROM item WHERE {COLOR_FIELD} IN ({in_list})"
        try:
            rows = client.suiteql(query)
        except Exception as exc:  # noqa: BLE001
            print(f"QUERY FAILED: {query!r}")
            print(f"  {str(exc)[:200]} :: {str(getattr(exc, 'payload', ''))[:500]}")
            raise
        for r in rows:
            item_id, retire_id = str(r["id"]), str(r["c"])
            canonical = plan.get(retire_id)
            if canonical is None:
                continue
            if max_items and considered >= max_items:
                continue
            considered += 1
            if samples < 10:
                samples += 1
                print(f"  sample: item {item_id} color {retire_id} -> {canonical}")
            if not allow_write:
                written += 1
                continue
            try:
                client.update_record("inventoryItem", item_id, {COLOR_FIELD: {"id": canonical}})
                written += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if failures <= 10:
                    detail = getattr(exc, "payload", "")
                    print(f"  FAILED item {item_id}: {str(exc)[:100]} :: {str(detail)[:200]}")

    verb = "repointed" if allow_write else "WOULD repoint (dry run)"
    print(f"\ncolor consolidation: {verb} {written} item(s); considered: {considered}; "
          f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

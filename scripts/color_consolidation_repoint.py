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

    # WHERE {COLOR_FIELD} IN (...) is rejected by SuiteQL ("Invalid or
    # unsupported search") -- custom-field equality filters don't translate.
    # IS NOT NULL does work (proven by the planner), so pull every colored
    # item and filter against the plan here.
    # (the WHERE doesn't actually filter -- all rows come back and SuiteQL
    # omits null columns from row JSON, hence .get below)
    rows = client.suiteql(
        f"SELECT id, {COLOR_FIELD} AS c FROM item WHERE {COLOR_FIELD} IS NOT NULL"
    )
    print(f"item rows fetched: {len(rows):,}")
    todo = []
    for r in rows:
        c = str(r.get("c") or "")
        if c in plan:
            todo.append((str(r["id"]), c))
    print(f"items pointing at a retired value: {len(todo):,}")

    considered = written = failures = 0
    samples = 0
    for item_id, retire_id in todo:
        canonical = plan[retire_id]
        if max_items and considered >= max_items:
            continue
        considered += 1
        if samples < 10:
            samples += 1
            print(f"  sample: item {item_id} color {retire_id} -> {canonical}")
        if not allow_write:
            written += 1
            continue
        # Matrix children expose the color under the matrixoption alias (the
        # bare custitem field is rejected with "Invalid value"); the bare
        # field stays as fallback for any non-matrix item.
        last_exc: Exception | None = None
        for body in (
            {f"matrixoption{COLOR_FIELD}": {"id": canonical}},
            {COLOR_FIELD: {"id": canonical}},
        ):
            try:
                client.update_record("inventoryItem", item_id, body)
                written += 1
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        if last_exc is not None:
            failures += 1
            if failures <= 10:
                detail = getattr(last_exc, "payload", "")
                print(f"  FAILED item {item_id}: {str(last_exc)[:150]} :: {str(detail)[:600]}")
                try:
                    who = client.suiteql(
                        "SELECT id, itemid, parent, isinactive, matrixtype "
                        f"FROM item WHERE id = {int(item_id)}"
                    )
                    print(f"    identity: {who}")
                except Exception as exc2:  # noqa: BLE001
                    print(f"    identity lookup failed: {str(exc2)[:120]}")

    verb = "repointed" if allow_write else "WOULD repoint (dry run)"
    print(f"\ncolor consolidation: {verb} {written} item(s); considered: {considered}; "
          f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

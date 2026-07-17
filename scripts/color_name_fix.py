"""Rename item-name color segments to canonical color-list names (CI).

Drives off two review artifacts committed in data/:
* items_production.csv -- the user's production item export (the fix set)
* color_token_mapping_final.csv -- token -> canonical name, statuses
  auto/user/inferred/inferred_low are applied; artifact/NOT_IN_LIST/
  unresolved rows are ignored (artifacts aren't color problems, the rest
  await user decisions).

For each item whose name parses as STYLE-COLOR-SIZE with a mapped color
token, the target name swaps the color segment: PC450-Blk-Small ->
PC450-Black-Small. Sandbox items are located by their CURRENT name; renames
that would collide with an existing item name (or with another rename in
the same batch) are skipped and reported, never forced. Honors
``SYNC_DRY_RUN``; ``UPDATE_MAX_ITEMS`` caps writes.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.adopt import split_color_size
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape

ROOT = Path(__file__).resolve().parents[1]
APPLY_STATUSES = {"auto", "user", "inferred", "inferred_low", "user_new_color"}


def load_mapping() -> dict[str, str]:
    out: dict[str, str] = {}
    with (ROOT / "data" / "color_token_mapping_final.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["status"] in APPLY_STATUSES and r["final_name"].strip():
                out[r["token"]] = r["final_name"].strip()
    return out


def plan_renames(mapping: dict[str, str]) -> dict[str, str]:
    """old itemid -> new itemid, from the production export."""
    renames: dict[str, str] = {}
    with (ROOT / "data" / "items_production.csv").open(
        encoding="utf-8-sig", newline=""
    ) as fh:
        for r in csv.DictReader(fh):
            itemid = (r.get("Name") or "").strip()
            style = (r.get("Vendor Name") or "").strip()
            if not style or itemid == style:
                continue
            cs = split_color_size(itemid, style)
            if not cs:
                continue
            color, size = cs
            new_color = mapping.get(color)
            if not new_color or new_color == color:
                continue
            renames[itemid] = f"{style}-{new_color}-{size}"
    return renames


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    client = NetSuiteClient(cfg.netsuite)

    mapping = load_mapping()
    print(f"applied mappings: {len(mapping)} tokens")
    renames = plan_renames(mapping)
    print(f"planned renames: {len(renames):,}")

    # Intra-batch collisions: two olds -> same new name.
    by_new: dict[str, list[str]] = {}
    for old, new in renames.items():
        by_new.setdefault(new, []).append(old)
    dupes = {new: olds for new, olds in by_new.items() if len(olds) > 1}
    for new, olds in list(dupes.items())[:10]:
        print(f"  INTRA-BATCH COLLISION {new!r} <- {olds}")
    renames = {o: n for o, n in renames.items() if n not in dupes}

    # Existing-name collisions: the target name is already taken in NetSuite.
    targets = sorted(set(renames.values()))
    taken: set[str] = set()
    for i in range(0, len(targets), 250):
        chunk = targets[i : i + 250]
        in_list = ", ".join(f"'{_sql_escape(t)}'" for t in chunk)
        for r in client.suiteql(f"SELECT itemid FROM item WHERE itemid IN ({in_list})"):
            taken.add(str(r["itemid"]))
    collisions = {o: n for o, n in renames.items() if n in taken}
    for old, new in list(collisions.items())[:10]:
        print(f"  NAME TAKEN {old!r} -> {new!r} (target already exists)")
    renames = {o: n for o, n in renames.items() if n not in taken}
    print(f"renames after collision checks: {len(renames):,} "
          f"(intra-batch dropped: {sum(len(v) for v in dupes.values())}, "
          f"target-taken dropped: {len(collisions)})")

    olds = sorted(renames)
    considered = written = missing = failures = 0
    samples = 0
    for i in range(0, len(olds), 250):
        chunk = olds[i : i + 250]
        in_list = ", ".join(f"'{_sql_escape(o)}'" for o in chunk)
        found = {
            str(r["itemid"]): str(r["id"])
            for r in client.suiteql(
                f"SELECT id, itemid FROM item WHERE itemid IN ({in_list})"
            )
        }
        for old in chunk:
            rid = found.get(old)
            if rid is None:
                missing += 1
                continue
            if max_items and considered >= max_items:
                continue
            considered += 1
            new = renames[old]
            if samples < 10:
                samples += 1
                print(f"  sample: {old!r} -> {new!r} (id {rid})")
            if not allow_write:
                written += 1
                continue
            try:
                client.update_record("inventoryItem", rid, {"itemId": new})
                written += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if failures <= 10:
                    detail = getattr(exc, "payload", "")
                    print(f"  FAILED {old!r}: {str(exc)[:100]} :: {str(detail)[:200]}")

    verb = "renamed" if allow_write else "WOULD rename (dry run)"
    print(f"\ncolor name fix: {verb} {written} item(s); "
          f"not found in this account: {missing}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Apply the per-STYLE colour corrections (item writes).

Consumes ``data/color_corrections.csv`` (style, current_color, correct_color)
and, for each style, repoints ONLY that style's items sitting on the wrong
colour onto the correct (vendor) value -- e.g. 695HBM's Columbia Blue items ->
Collegiate Blue. Scoped to the style, so a Columbia Blue item of any OTHER style
is never touched. A correction whose target colour value doesn't exist yet is
skipped with a warning (create the value first, then re-run). Diff-aware;
honours ``SYNC_DRY_RUN``; ``UPDATE_MAX_ITEMS`` caps writes.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.adopt import COLOR_FIELD, OptionMaps
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape

ROOT = Path(__file__).resolve().parents[1]


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def load_corrections() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    with (ROOT / "data" / "color_corrections.csv").open(
        encoding="utf-8-sig", newline=""
    ) as fh:
        for r in csv.DictReader(fh):
            st = (r.get("style") or "").strip()
            cur = (r.get("current_color") or "").strip()
            cor = (r.get("correct_color") or "").strip()
            if st and cur and cor:
                rows.append((st, cur, cor))
    return rows


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    client = NetSuiteClient(cfg.netsuite)

    corrections = load_corrections()
    options = OptionMaps(client)

    # Build the work list: (item_id, correct_color_id, matrixtype).
    todo: list[tuple[str, str, str, str]] = []  # id, correct_id, matrixtype, label
    skipped_no_target: list[tuple[str, str]] = []
    style_items: dict[str, list[dict]] = {}
    for style, cur, cor in corrections:
        cor_ids = options.color_by_name.get(_norm(cor), [])
        if not cor_ids:
            skipped_no_target.append((style, cor))
            continue
        correct_id = cor_ids[0]
        cur_ids = set(options.color_by_name.get(_norm(cur), []))
        if style not in style_items:
            safe = _sql_escape(style)
            style_items[style] = client.suiteql(
                f"SELECT id, itemid, {COLOR_FIELD} AS color, isinactive, matrixtype "
                f"FROM item WHERE vendorname = '{safe}'"
            )
        for r in style_items[style]:
            if str(r.get("color") or "") not in cur_ids:
                continue
            if str(r.get("isinactive") or "F") == "T":
                continue  # inactive children reject option writes
            todo.append((str(r["id"]), correct_id, str(r.get("matrixtype") or ""),
                         f"{r.get('itemid')} {cur}->{cor}"))

    for style, cor in skipped_no_target:
        print(f"SKIP {style}: target colour {cor!r} does not exist yet -- "
              f"create it, then re-run to move its items")
    print(f"items to correct: {len(todo)}")

    considered = written = failures = 0
    for item_id, correct_id, matrixtype, label in todo:
        if max_items and considered >= max_items:
            continue
        considered += 1
        if considered <= 10:
            print(f"  {label} (item {item_id} -> colour {correct_id})")
        if not allow_write:
            written += 1
            continue
        if matrixtype == "CHILD":
            body = {f"matrixoption{COLOR_FIELD}": {"id": correct_id}}
        else:
            body = {COLOR_FIELD: {"items": [{"id": correct_id}]}}
        try:
            client.update_record("inventoryItem", item_id, body)
            written += 1
        except Exception as exc:  # noqa: BLE001
            failures += 1
            if failures <= 10:
                detail = getattr(exc, "payload", "")
                print(f"  FAILED item {item_id}: {str(exc)[:150]} :: {str(detail)[:300]}")

    verb = "corrected" if allow_write else "WOULD correct (dry run)"
    print(f"\ncolor correction: {verb} {written} item(s); considered: {considered}; "
          f"skipped styles (no target value): {len(skipped_no_target)}; "
          f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Apply the per-STYLE colour corrections (item writes).

Consumes ``data/color_corrections.csv`` (style, current_color, correct_color)
and, for each style, repoints ONLY that style's items sitting on the wrong
colour onto the correct (vendor) value -- e.g. 695HBM's Columbia Blue items ->
Collegiate Blue. Scoped to the style, so a Columbia Blue item of any OTHER style
is never touched.

Two writes per item, because NetSuite only regenerates a matrix child's name
from the parent's name template ({itemid}-{custitem_bsg_color}-{custitem_bsg_
size}) on a matrix create/update -- changing the option by API leaves the stored
Item Name/Number stale:

1. the colour matrix option (custitem_bsg_color) -> the correct value, and
2. the Item Name/Number -- the old colour segment in the name is replaced with
   the new one (695HBM-Columbia Blue-Small -> 695HBM-Collegiate Blue-Small).

An item is picked up if its colour is still the wrong value OR its name still
carries the old colour, so a re-run finishes items that were only half-fixed. A
missing correct colour value is created first (create_record on the colour
list). Matrix-child aware, diff-aware; honours ``SYNC_DRY_RUN``;
``UPDATE_MAX_ITEMS`` caps item writes.
"""

from __future__ import annotations

import csv
import os
import re
from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.adopt import COLOR_FIELD, COLOR_LIST, OptionMaps
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


def rename_in_itemid(itemid: str, cur: str, cor: str) -> str:
    """Replace the ``-<current colour>-`` segment in an item name with the new
    colour, e.g. 695HBM-Columbia Blue-Small -> 695HBM-Collegiate Blue-Small."""
    return re.sub(re.escape(f"-{cur}-"), f"-{cor}-", itemid, count=1,
                  flags=re.IGNORECASE)


def create_color_value(client: NetSuiteClient, name: str) -> str:
    """Create a colour-list value; returns its new internal id."""
    body = {"name": name, "abbreviation": name}
    try:
        return str(client.create_record(COLOR_LIST, body))
    except Exception as exc:  # noqa: BLE001
        if "abbreviation" in str(getattr(exc, "payload", "")).lower():
            body["abbreviation"] = f"{name[:12]}~"
            return str(client.create_record(COLOR_LIST, body))
        raise


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    client = NetSuiteClient(cfg.netsuite)

    corrections = load_corrections()
    options = OptionMaps(client)

    created: dict[str, str] = {}
    would_create: dict[str, str] = {}
    # (item_id, matrixtype, body, label)
    todo: list[tuple[str, str, dict, str]] = []
    style_items: dict[str, list[dict]] = {}

    for style, cur, cor in corrections:
        ncor = _norm(cor)
        cor_ids = options.color_by_name.get(ncor, [])
        if cor_ids:
            correct_id: str | None = cor_ids[0]
        elif ncor in created:
            correct_id = created[ncor]
        elif allow_write:
            correct_id = create_color_value(client, cor)
            created[ncor] = correct_id
            print(f"created colour value {cor!r} (id {correct_id})")
        else:
            would_create.setdefault(ncor, cor)
            correct_id = None

        cur_ids = set(options.color_by_name.get(_norm(cur), []))
        seg = f"-{cur}-".lower()
        if style not in style_items:
            safe = _sql_escape(style)
            style_items[style] = client.suiteql(
                f"SELECT id, itemid, {COLOR_FIELD} AS color, isinactive, matrixtype "
                f"FROM item WHERE vendorname = '{safe}'"
            )
        for r in style_items[style]:
            itemid = str(r.get("itemid") or "")
            color_now = str(r.get("color") or "")
            wrong_color = color_now in cur_ids
            stale_name = seg in itemid.lower()
            if not (wrong_color or stale_name):
                continue
            if str(r.get("isinactive") or "F") == "T":
                continue  # inactive children reject option writes
            matrixtype = str(r.get("matrixtype") or "")
            body: dict = {}
            # colour option (only if not already the correct value)
            if correct_id is not None and color_now != correct_id:
                if matrixtype == "CHILD":
                    body[f"matrixoption{COLOR_FIELD}"] = {"id": correct_id}
                else:
                    body[COLOR_FIELD] = {"items": [{"id": correct_id}]}
            elif correct_id is None and wrong_color:
                body["_pending_color"] = True  # dry: value would be created
            # Item Name/Number (only if it still carries the old colour)
            if stale_name:
                new_itemid = rename_in_itemid(itemid, cur, cor)
                if new_itemid != itemid:
                    body["itemid"] = new_itemid
            if body:
                todo.append((str(r["id"]), matrixtype, body,
                             f"{itemid} [{cur}->{cor}]"))

    for _n, name in would_create.items():
        print(f"WOULD create colour value {name!r} (dry run)")
    print(f"items to fix: {len(todo)}")

    considered = written = failures = 0
    for item_id, _mt, body, label in todo:
        if max_items and considered >= max_items:
            continue
        considered += 1
        writes = [k for k in body if not k.startswith("_")]
        if considered <= 12:
            print(f"  {label}: set {writes} (item {item_id})")
        body.pop("_pending_color", None)
        if not allow_write or not body:
            written += 1  # dry run, or nothing writable yet (value pending)
            continue
        try:
            client.update_record("inventoryItem", item_id, body)
            written += 1
        except Exception as exc:  # noqa: BLE001
            failures += 1
            if failures <= 10:
                detail = getattr(exc, "payload", "")
                print(f"  FAILED item {item_id}: {str(exc)[:150]} :: {str(detail)[:300]}")

    verb = "fixed" if allow_write else "WOULD fix (dry run)"
    print(f"\ncolor correction: {verb} {written} item(s); considered: {considered}; "
          f"values created: {len(created)}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

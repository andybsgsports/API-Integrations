"""Apply the per-STYLE colour corrections (item writes).

Consumes ``data/color_corrections.csv`` (style, current_color, correct_color)
and, for each style, repoints ONLY that style's items sitting on the wrong
colour onto the correct (vendor) value -- e.g. 695HBM's Columbia Blue items ->
Collegiate Blue. Scoped to the style, so a Columbia Blue item of any OTHER style
is never touched.

If a correct (vendor) colour value doesn't exist yet, it is CREATED on the
colour list (same REST record type the repoint writer inactivates values on),
so no manual step is needed. Matrix-child aware, diff-aware; honours
``SYNC_DRY_RUN``; ``UPDATE_MAX_ITEMS`` caps item writes.
"""

from __future__ import annotations

import csv
import os
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


def create_color_value(client: NetSuiteClient, name: str) -> str:
    """Create a colour-list value; returns its new internal id.

    The abbreviation is required and must be unique -- if it collides, retry
    with an id-suffixed abbreviation (mirrors the repoint writer's approach).
    """
    body = {"name": name, "abbreviation": name}
    try:
        return str(client.create_record(COLOR_LIST, body))
    except Exception as exc:  # noqa: BLE001
        payload = str(getattr(exc, "payload", "")).lower()
        if "abbreviation" in payload:
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

    created: dict[str, str] = {}       # norm(correct) -> new id (live)
    would_create: dict[str, str] = {}  # norm(correct) -> display name (dry)
    todo: list[tuple[str, str | None, str, str]] = []  # id, correct_id, mtype, label
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
            correct_id = None  # dry: pretend it will exist

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

    for _n, name in would_create.items():
        print(f"WOULD create colour value {name!r} (dry run)")
    print(f"items to correct: {len(todo)}")

    considered = written = failures = 0
    for item_id, correct_id, matrixtype, label in todo:
        if max_items and considered >= max_items:
            continue
        considered += 1
        if considered <= 12:
            print(f"  {label} (item {item_id} -> colour {correct_id or 'PENDING-CREATE'})")
        if not allow_write or correct_id is None:
            written += 1  # dry run (or target pending creation)
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
          f"values created: {len(created)}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

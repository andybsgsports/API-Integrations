"""Finalize newly-created SanMar matrix children: fill the structural fields the
CSV create-import can't set on a child -- Department, Class, Purchase / Sales
Description (copied from the parent), and Location (set to Badger Sporting Goods).

The CSV create-import can't reliably set list-reference fields on a matrix child,
so children created that way land with Department / Class / descriptions blank
even though the parent carries the correct values. Two sources of truth:

* **Copied from the PARENT** (Department, Class, Sales/Purchase Description) --
  the parent's reference columns come back from SuiteQL as internal ids, so the
  copy is exact (no name->id lookup). Fill-blanks-only: written only when the
  parent has a value and the child is blank; never overwrites the child.
* **Fixed value** (Location = ``Badger Sporting Goods``) -- resolved to its
  internal id once and set on every SanMar child that isn't already on it.

Robustness rules learned the hard way:
* **Every candidate column is probed** (``SELECT col FROM item WHERE rownum<=1``)
  before it's used -- one bad column name 400s the whole query, so we only
  SELECT columns NetSuite actually projects.
* **SanMar children only** (``externalid LIKE 'SANMAR%'``).
* Parent-chunked scan (SuiteQL caps a result window at 100k rows).
* Dry-run by default (``SYNC_DRY_RUN``); ``UPDATE_MAX_ITEMS`` caps writes.

Units of measure and costing method are intentionally NOT handled here: on a
matrix item those are parent-defined and locked at create, so they can't be
PATCHed onto an existing child. Income account + Base Price -> ``reconcile-items``.
"""

from __future__ import annotations

import os

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape

# (SuiteQL column, REST field, is a List/Record reference). Reference columns
# come back from SuiteQL as the internal id, so the copy is {"id": <that id>}.
# These are the item fields parent_sync.py already writes -- proven patchable.
COPY_FIELDS: list[tuple[str, str, bool]] = [
    ("department", "department", True),
    ("class", "class", True),
    ("description", "salesDescription", False),          # SuiteQL col is 'description'
    ("purchasedescription", "purchaseDescription", False),
]
LOCATION_NAME = "Badger Sporting Goods"


def _valid_cols(client: NetSuiteClient, candidates: list[str]) -> list[str]:
    """Keep only columns that actually project (a bad one 400s the whole query)."""
    ok: list[str] = []
    for col in candidates:
        try:
            client.suiteql(f"SELECT {col} FROM item WHERE rownum <= 1")
            ok.append(col)
        except Exception:  # noqa: BLE001 - column absent/unqueryable; skip it
            print(f"  (column {col!r} does not project; skipping)")
    return ok


def _resolve_location_id(client: NetSuiteClient, name: str) -> str | None:
    rows = client.suiteql(f"SELECT id FROM location WHERE name = '{_sql_escape(name)}'")
    return str(rows[0]["id"]) if rows else None


def plan_body(
    child: dict,
    parent: dict,
    *,
    copy_cols: list[tuple[str, str, bool]],
    location_id: str | None,
    has_location_col: bool,
) -> dict:
    """REST body for one child.

    Copy fields: written only when the parent has a value and the child is blank.
    Location: a fixed value -- set whenever the child isn't already on it (if the
    ``location`` column doesn't project we can't diff, so set it unconditionally).
    """
    body: dict[str, object] = {}
    for col, rest, is_ref in copy_cols:
        pval = str(parent.get(col) or "").strip()
        cval = str(child.get(col) or "").strip()
        if pval and not cval:
            body[rest] = {"id": pval} if is_ref else pval
    if location_id:
        current = str(child.get("location") or "").strip()
        if not has_location_col or current != location_id:
            # Warehouse + Preferred Location both = Badger Sporting Goods.
            body["location"] = {"id": location_id}
            body["preferredLocation"] = {"id": location_id}
    return body


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    client = NetSuiteClient(cfg.netsuite)

    location_id = _resolve_location_id(client, LOCATION_NAME)
    if location_id:
        print(f"location {LOCATION_NAME!r} -> internal id {location_id}")
    else:
        print(f"WARNING: location {LOCATION_NAME!r} not found; leaving location unset")

    # Probe copy columns + the optional location column so a bad name can't
    # 400 the run.
    copy_cols = [t for t in COPY_FIELDS
                 if t[0] in _valid_cols(client, [t[0] for t in COPY_FIELDS])]
    has_location_col = bool(location_id) and "location" in _valid_cols(client, ["location"])
    read_cols = [t[0] for t in copy_cols] + (["location"] if has_location_col else [])
    cols = ", ".join(read_cols)
    col_sql = f", {cols}" if cols else ""

    parents = client.suiteql(f"SELECT id, itemid{col_sql} FROM item WHERE parent IS NULL")
    parents_by_id = {str(p["id"]): p for p in parents}
    print(f"candidate parent items: {len(parents):,}")

    parent_ids = list(parents_by_id)
    considered = written = unchanged = failures = scanned = 0
    samples = 0
    for i in range(0, len(parent_ids), 250):
        chunk = ", ".join(parent_ids[i : i + 250])
        children = client.suiteql(
            f"SELECT id, parent, itemid{col_sql} FROM item "
            f"WHERE parent IN ({chunk}) AND externalid LIKE 'SANMAR%'"
        )
        for child in children:
            scanned += 1
            parent = parents_by_id.get(str(child.get("parent") or ""), {})
            body = plan_body(
                child, parent, copy_cols=copy_cols,
                location_id=location_id, has_location_col=has_location_col,
            )
            if not body:
                unchanged += 1
                continue
            if max_items and considered >= max_items:
                continue
            considered += 1
            if samples < 12:
                samples += 1
                print(f"  {child.get('itemid')} (id {child.get('id')}): set "
                      f"{', '.join(body)}")
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

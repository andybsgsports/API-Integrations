"""Fill Units of Measure, Weight Unit, and Store Description on every matrix item
that's missing them.

These were wrongly dropped from the create-import (I excluded them as "inherited"
-- they're actually per-item settable, as item 161098 shows). Rather than guess
the UOM record ids, this reads a REFERENCE item that already has them and copies
its Units Type / Stock / Purchase / Sale Unit + Weight Unit down. Store
Description is set from each item's own sales (detailed) description.

Diff-aware (writes only what's blank), id-range-chunked, dry-run by default. The
dry run prints the reference values it would copy, so they can be eyeballed
first. ``UPDATE_MAX_ITEMS`` caps writes; ``UOM_REFERENCE_ID`` overrides the
reference item.
"""

from __future__ import annotations

import os

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

# REST field -> its SuiteQL column, for the "is it blank?" scan.
UOM_FIELDS = {
    "unitsType": "unitstype",
    "stockUnit": "stockunit",
    "purchaseUnit": "purchaseunit",
    "saleUnit": "saleunit",
    "weightUnit": "weightunit",
}


def _ref(value):
    """Normalise a GET field value into something PATCH accepts."""
    if isinstance(value, dict) and value.get("id") is not None:
        return {"id": str(value["id"])}
    return value  # enum string (e.g. weightUnit "lb")


def _projects(client: NetSuiteClient, col: str) -> bool:
    try:
        client.suiteql(f"SELECT {col} FROM item WHERE rownum <= 1")
        return True
    except Exception:  # noqa: BLE001
        return False


def build_body(row: dict, uom: dict, has_uom_col: bool) -> dict:
    """Fields to set on one item: UOM (if its unitstype is blank) + Store
    Description (= the item's own sales description, if blank/different)."""
    body: dict = {}
    if not has_uom_col or not str(row.get("unitstype") or "").strip():
        body.update(uom)
    # SuiteQL sales-description column is 'description' (salesdescription doesn't
    # project). storedescription may be absent from the row -> treated as blank,
    # so it still gets set from the item's own description.
    sales = str(row.get("description") or "").strip()
    store = str(row.get("storedescription") or "").strip()
    if sales and store != sales:
        body["storeDescription"] = sales
    return body


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    chunk = int(os.environ.get("LOCATION_FIX_CHUNK", "40000") or "40000")
    client = NetSuiteClient(cfg.netsuite)

    # Reference item that already carries UOM -- copy its ids.
    ref_id = os.environ.get("UOM_REFERENCE_ID", "").strip()
    has_uom_col = _projects(client, "unitstype")
    if not ref_id:
        if not has_uom_col:
            print("cannot find a UOM reference (unitstype doesn't project); set UOM_REFERENCE_ID")
            return 1
        rows = client.suiteql(
            "SELECT id FROM item WHERE matrixtype IN ('PARENT', 'CHILD') "
            "AND unitstype IS NOT NULL AND rownum <= 1"
        )
        if not rows:
            print("no item with a Units Type found to use as a reference")
            return 1
        ref_id = str(rows[0]["id"])
    ref = client.get_record("inventoryItem", ref_id)
    uom = {f: _ref(ref[f]) for f in UOM_FIELDS if ref.get(f)}
    print(f"reference item {ref_id}: copying {uom}")
    if not uom:
        print("reference item has no UOM fields set -- pick another UOM_REFERENCE_ID")
        return 1

    store_ok = _projects(client, "storedescription")
    cols = "id, description" + (", unitstype" if has_uom_col else "") \
        + (", storedescription" if store_ok else "")

    max_rows = client.suiteql(
        "SELECT MAX(id) AS m FROM item WHERE matrixtype IN ('PARENT', 'CHILD')"
    )
    max_id = int(max_rows[0]["m"]) if max_rows and max_rows[0].get("m") else 0
    print(f"scanning matrix items up to id {max_id} in chunks of {chunk}")

    considered = written = failures = scanned = 0
    samples = 0
    for lo in range(0, max_id + 1, chunk):
        hi = lo + chunk - 1
        items = client.suiteql(
            f"SELECT {cols} FROM item "
            f"WHERE matrixtype IN ('PARENT', 'CHILD') AND id BETWEEN {lo} AND {hi}"
        )
        for r in items:
            scanned += 1
            body = build_body(r, uom, has_uom_col)
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
            try:
                client.update_record("inventoryItem", str(r["id"]), body)
                written += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if failures <= 10:
                    print(f"  FAILED item {r.get('id')}: {str(exc)[:120]} "
                          f":: {str(getattr(exc, 'payload', ''))[:250]}")

    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(f"\nuom/store fix: {verb} {written} item(s); scanned: {scanned:,}; "
          f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

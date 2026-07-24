"""Read-only probe of NetSuite's Units-of-Measure setup.

Goal: find the ids needed to set bottoms (shorts/pants/leggings/...) to a
"Pair(s)" unit instead of "Each". An item's unitsType points to a Units Type
record; stockUnit / purchaseUnit / saleUnit point to a specific unit within
it. To switch bottoms to Pair we need to know whether "Pair" is a unit inside
the existing (Each) Units Type or a separate Units Type, and its ids.

Dumps every Units Type and its units, flags anything matching pair/each, and
samples what a current bottoms item actually carries. Never writes; always
exits 0 (a "not found" answer is the diagnosis, not a CI failure).
"""

from __future__ import annotations

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient


def _q(client, label: str, query: str) -> list[dict]:
    print(f"\n== {label} ==")
    try:
        rows = client.suiteql(query)
    except Exception as exc:  # noqa: BLE001 - table/column may differ by account
        print(f"  (query failed: {str(exc)[:200]})")
        return []
    if not rows:
        print("  (no rows)")
    for r in rows:
        print("  " + ", ".join(f"{k}={v}" for k, v in r.items()))
    return rows


def main() -> int:
    client = NetSuiteClient(get_config().netsuite)

    # 1. Units Type records (the group an item's unitsType points at).
    _q(client, "unitstype records", "SELECT id, name FROM unitstype ORDER BY id")

    # 2. The individual units inside each type. Table name varies; try the
    #    common ones. This is where 'Each' vs 'Pair' actually live.
    for tbl in ("unitstypeuom", "unitsTypeUom", "unit"):
        rows = _q(
            client,
            f"units in {tbl} (name/plural/abbrev, base flag, conversion)",
            f"SELECT * FROM {tbl}",
        )
        if rows:
            pairs = [
                r for r in rows
                if any("pair" in str(v).lower() for v in r.values())
            ]
            print(f"  -> rows mentioning 'pair': {len(pairs)}")
            for r in pairs:
                print("     PAIR? " + ", ".join(f"{k}={v}" for k, v in r.items()))
            break

    # 3. The REST id space (the important part -- writing id 13 for the Pair
    #    unit was rejected, so REST uses different ids than unitstypeuom).
    #    GET a real item that already has UOM set and print its unit refs
    #    verbatim: that shows what a stock/purchase/sale unit id looks like.
    print("\n== REST view of a UOM-bearing item (real id format) ==")
    try:
        ref_rows = client.suiteql(
            "SELECT id FROM item WHERE unitstype IS NOT NULL "
            "AND matrixtype IN ('PARENT', 'CHILD') AND rownum <= 1"
        )
        if ref_rows:
            rid = str(ref_rows[0]["id"])
            rec = client.get_record("inventoryItem", rid)
            for f in ("unitsType", "stockUnit", "purchaseUnit", "saleUnit"):
                print(f"  {f}: {rec.get(f)}")
        else:
            print("  (no UOM-bearing item found)")
    except Exception as exc:  # noqa: BLE001
        print(f"  (item GET failed: {str(exc)[:200]})")

    # 4. The unitsType record itself, via REST -- its member units carry the
    #    ids REST wants for stock/purchase/sale unit. Try Pair (6) and Each (1).
    for utype in ("1", "6"):
        print(f"\n== REST unitsType/{utype} (member units + their ids) ==")
        try:
            rec = client.get_record("unitsType", utype)
            print(f"  name: {rec.get('name')}")
            uoms = rec.get("uom") or rec.get("uomList") or {}
            items = uoms.get("items") if isinstance(uoms, dict) else uoms
            for u in (items or []):
                print(f"    unit id={u.get('internalId') or u.get('id')} "
                      f"name={u.get('unitName') or u.get('name')} "
                      f"base={u.get('baseUnit')} abbr={u.get('abbreviation')}")
            if not items:
                print(f"  raw: {rec}")
        except Exception as exc:  # noqa: BLE001
            print(f"  (unitsType GET failed: {str(exc)[:200]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

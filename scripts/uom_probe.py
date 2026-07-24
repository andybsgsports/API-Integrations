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

    # 3. What a real bottoms item carries today (from the screenshot: Ladies
    #    B-Core Shorts, style 411600). Confirms the fields we'd rewrite.
    _q(
        client,
        "sample bottoms item UOM (style 411600 = B-Core Shorts)",
        "SELECT id, itemid, unitstype, stockunit, purchaseunit, saleunit "
        "FROM item WHERE custitem_sanmar_style = '411600' AND rownum <= 3",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Legacy catalog coverage report, read-only (runs on CI).

Answers: how much of the item catalog is being kept current by a supplier
feed, and which unmatched ACTIVE items would pay off most to match next?
Sales-activity ranking is attempted from transaction lines (last 12 months)
and skipped gracefully if the query isn't available to this role.
"""

from __future__ import annotations

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

KEY_FIELDS = [
    "custitem_sanmar_unique_key",
    "custitem_ss_sku",
    "custitem_mtec_item_sku",
    "custitem_ua_part_id",
]


def main() -> int:
    client = NetSuiteClient(get_config().netsuite)
    matched = " OR ".join(f"{f} IS NOT NULL" for f in KEY_FIELDS)

    def count(where: str) -> int:
        return int(client.suiteql(f"SELECT COUNT(*) AS n FROM item WHERE {where}")[0]["n"])

    total = count("1 = 1")
    active = count("isinactive = 'F'")
    matched_total = count(matched)
    matched_active = count(f"isinactive = 'F' AND ({matched})")
    unmatched_active = active - matched_active
    print("=== catalog coverage ===")
    print(f"items total:                {total:,}")
    print(f"items active:               {active:,}")
    print(f"supplier-matched:           {matched_total:,}")
    print(f"supplier-matched active:    {matched_active:,}")
    print(f"UNMATCHED active:           {unmatched_active:,} "
          f"({100.0 * unmatched_active / active:.1f}% of active)")

    print("\n=== unmatched active items by type ===")
    try:
        for r in client.suiteql(
            "SELECT itemtype, COUNT(*) AS n FROM item "
            f"WHERE isinactive = 'F' AND NOT ({matched}) "
            "GROUP BY itemtype ORDER BY COUNT(*) DESC"
        ):
            print(f"  {r.get('itemtype')}: {int(r['n']):,}")
    except Exception as exc:  # noqa: BLE001
        print(f"  (breakdown query failed: {str(exc)[:150]})")

    print("\n=== top unmatched active items by sales activity (last 12 months) ===")
    try:
        rows = client.suiteql(
            "SELECT tl.item AS item_id, i.itemid, SUM(ABS(tl.quantity)) AS qty "
            "FROM transactionline tl "
            "JOIN transaction t ON t.id = tl.transaction "
            "JOIN item i ON i.id = tl.item "
            "WHERE t.trandate >= ADD_MONTHS(SYSDATE, -12) "
            "AND t.type = 'SalesOrd' "
            f"AND i.isinactive = 'F' AND NOT ({matched.replace('custitem', 'i.custitem')}) "
            "GROUP BY tl.item, i.itemid "
            "ORDER BY SUM(ABS(tl.quantity)) DESC "
            "FETCH FIRST 50 ROWS ONLY"
        )
        if not rows:
            print("  (no sales lines found for unmatched active items)")
        for r in rows:
            print(f"  {r.get('itemid')}: {int(float(r['qty'])):,} units")
    except Exception as exc:  # noqa: BLE001
        print(f"  (sales-activity query unavailable: {str(exc)[:200]})")
        print("  -- falling back: sample of unmatched active inventory items --")
        try:
            for r in client.suiteql(
                "SELECT itemid, displayname FROM item "
                f"WHERE isinactive = 'F' AND itemtype = 'InvtPart' AND NOT ({matched}) "
                "FETCH FIRST 25 ROWS ONLY"
            ):
                print(f"  {r.get('itemid')}: {str(r.get('displayname') or '')[:60]}")
        except Exception as exc2:  # noqa: BLE001
            print(f"  (fallback failed too: {str(exc2)[:150]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Find the vendor records (internal ids) for the supplier sublist work.

Read-only: lists vendors whose names look like our four suppliers, plus the
distinct vendor references already used on matched items.
"""

from __future__ import annotations

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

PATTERNS = ["sanmar", "s%26s", "s&s", "alphabroder", "momentec", "augusta",
            "founder", "badger", "under armour", "underarmour"]


def main() -> int:
    client = NetSuiteClient(get_config().netsuite)
    print("=== vendor records matching supplier names ===")
    for pat in PATTERNS:
        safe = pat.replace("'", "''").replace("&", "' || CHR(38) || '")
        try:
            rows = client.suiteql(
                "SELECT id, companyname, entityid FROM vendor "
                f"WHERE LOWER(companyname) LIKE '%{safe.lower()}%' "
                f"   OR LOWER(entityid) LIKE '%{safe.lower()}%'"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ({pat}: query failed: {str(exc)[:80]})")
            continue
        for r in rows:
            print(f"  id={r['id']:<8} entityid={r.get('entityid','')!r:<30} "
                  f"company={r.get('companyname','')!r}")
    print("\n=== vendors currently referenced on items with a SanMar style ===")
    try:
        rows = client.suiteql(
            "SELECT DISTINCT itemvendor.vendor AS vid, COUNT(*) AS n "
            "FROM item JOIN itemvendor ON itemvendor.item = item.id "
            "WHERE item.custitem_sanmar_style IS NOT NULL "
            "GROUP BY itemvendor.vendor"
        )
        for r in rows:
            print(f"  vendor id {r.get('vid')}: {r.get('n')} item lines")
    except Exception as exc:  # noqa: BLE001
        print(f"  (itemvendor join failed: {str(exc)[:120]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

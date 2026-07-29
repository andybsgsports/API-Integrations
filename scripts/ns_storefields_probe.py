"""Read-only probe: did Store Display Name / Store Description actually persist?

The 2026-07-25 run wrote 45,531 records with zero failures and zero dropped
fields -- NetSuite accepted every PATCH, and those bodies contained
storeDisplayName/storeDescription. Yet SuiteQL still reports both columns as
populated on roughly 45 items. Exactly one of these is true:

* NetSuite accepted the write and silently discarded it (the field needs some
  other flag set, or is not writable this way), or
* the value IS on the record and SuiteQL's column simply doesn't report it,
  which would mean the audit is measuring the wrong thing.

A REST GET of records we just wrote distinguishes them: the record service is
the same surface the PATCH went to, so what it returns is what actually stuck.
Compares that against SuiteQL for the SAME ids, so the two views sit
side by side rather than being compared across different item populations.

Writes nothing.
"""

from __future__ import annotations

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

FIELDS = ("storeDisplayName", "storeDescription", "displayName", "isOnline")


def main() -> int:
    cfg = get_config()
    client = NetSuiteClient(cfg.netsuite)

    # THE question: are the ~50 items that DID keep a Store Display Name
    # matrix PARENTS, while the blank ones are children? JST55 (a parent)
    # holds the value; 18200B-Sports Grey-X-Large (a child) does not. If that
    # is the split, NetSuite simply does not persist web-store fields on
    # matrix children and no amount of retrying will change it.
    print("Items that DO carry a Store Display Name -- parent or child?")
    print("=" * 66)
    have = client.suiteql(
        "SELECT id, itemid, parent, storedisplayname FROM item "
        "WHERE storedisplayname IS NOT NULL AND rownum <= 12"
    )
    parents = sum(1 for r in have if not str(r.get("parent") or "").strip())
    for r in have[:8]:
        kind = "CHILD (parent=" + str(r.get("parent")) + ")" if str(
            r.get("parent") or "").strip() else "PARENT/standalone"
        print(f"  {r.get('itemid')}: {kind}")
    print(f"  -> {parents} of {len(have)} sampled are parents/standalone")

    print()
    print("Do any matrix CHILDREN carry one?")
    print("=" * 66)
    kids = client.suiteql(
        "SELECT COUNT(*) AS n FROM item "
        "WHERE storedisplayname IS NOT NULL AND parent IS NOT NULL"
    )
    par = client.suiteql(
        "SELECT COUNT(*) AS n FROM item "
        "WHERE storedisplayname IS NOT NULL AND parent IS NULL"
    )
    n_kids = int(kids[0]["n"]) if kids else 0
    n_par = int(par[0]["n"]) if par else 0
    print(f"  children with a Store Display Name: {n_kids:,}")
    print(f"  parents/standalone with one:        {n_par:,}")
    if n_kids == 0 and n_par:
        print("  -> CONFIRMED: only parents keep it. NetSuite discards the "
              "field on\n     matrix children; writing it there is futile.")
    print()

    rows = client.suiteql(
        "SELECT id, itemid, storedisplayname, storedescription, isonline "
        "FROM item WHERE custitem_sanmar_unique_key IS NOT NULL "
        "AND rownum <= 6"
    )
    print(f"comparing {len(rows)} SanMar item(s): SuiteQL vs REST GET\n")

    mismatches = 0
    for row in rows:
        rid = str(row["id"])
        print(f"item {rid} ({row.get('itemid')})")
        print("  SuiteQL :"
              f" storedisplayname={row.get('storedisplayname')!r}"
              f" storedescription={str(row.get('storedescription'))[:40]!r}"
              f" isonline={row.get('isonline')!r}")
        try:
            rec = client.get_record("inventoryItem", rid)
        except Exception as exc:  # noqa: BLE001 - probe reports, never crashes
            print(f"  REST    : GET failed: {str(exc)[:120]}")
            continue
        shown = {f: rec.get(f) for f in FIELDS}
        print("  REST    :"
              f" storeDisplayName={str(shown['storeDisplayName'])[:40]!r}"
              f" storeDescription={str(shown['storeDescription'])[:40]!r}"
              f" isOnline={shown['isOnline']!r}")

        # The question that matters: REST has it, SuiteQL doesn't report it.
        rest_has = bool(str(rec.get("storeDisplayName") or "").strip())
        sql_has = bool(str(row.get("storedisplayname") or "").strip())
        if rest_has != sql_has:
            mismatches += 1
            print("  -> MISMATCH: REST and SuiteQL disagree on storeDisplayName")
        print()

    print("=" * 70)
    if mismatches:
        print(f"{mismatches} item(s) where REST holds the value but SuiteQL does not "
              f"report it.\nThe write landed; the AUDIT is reading the wrong column.")
    else:
        print("REST and SuiteQL agree. If both are blank, NetSuite accepted the "
              "PATCH\nand discarded the value -- the field needs something else "
              "set to stick.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

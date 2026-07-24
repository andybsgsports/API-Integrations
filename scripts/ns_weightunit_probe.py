"""Read-only probe: what does NetSuite actually accept for ``weightUnit``?

The live run fails every record with ``Invalid Field Value lb for the following
field: weightunit``, and the value we send ("lb") came from a code comment
rather than a verified write -- so this asks NetSuite directly instead of
guessing again. A wrong guess costs a ~1h45m full run, so the answer has to come
from the account.

Two independent sources of truth:

1. SuiteQL ``GROUP BY weightunit`` -- every value currently stored on the 56k
   items, with counts. Shows the vocabulary the account really uses.
2. A REST ``GET`` of individual items that already carry a weight unit. The
   representation REST *returns* is the representation REST *accepts* on PATCH,
   which is the one that actually matters here (SuiteQL can spell it
   differently from the REST record service).

Writes nothing.
"""

from __future__ import annotations

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient


def main() -> int:
    cfg = get_config()
    client = NetSuiteClient(cfg.netsuite)

    print("=" * 70)
    print("1. Stored weightunit values (SuiteQL, whole account)")
    print("=" * 70)
    rows = client.suiteql(
        "SELECT weightunit AS wu, COUNT(*) AS n FROM item GROUP BY weightunit"
    )
    for r in sorted(rows, key=lambda x: -int(x.get("n") or 0)):
        raw = r.get("wu")
        n = int(r.get("n") or 0)
        print(f"  {str(raw)!r:<24} {n:>8,} item(s)")
    if not rows:
        print("  (no rows)")

    print()
    print("=" * 70)
    print("2. REST representation on items that carry a weight unit")
    print("=" * 70)
    # Sample a few items that already have the field set -- their REST payload
    # shows the exact shape/spelling a PATCH must use.
    sample = client.suiteql(
        "SELECT id FROM item WHERE weightunit IS NOT NULL AND rownum <= 5"
    )
    if not sample:
        print("  no item carries a weightunit -- nothing to compare against")
    for row in sample:
        rid = str(row["id"])
        try:
            rec = client.get_record("inventoryItem", rid)
        except Exception as exc:  # noqa: BLE001 - probe should report, not crash
            print(f"  item {rid}: GET failed: {str(exc)[:120]}")
            continue
        wu = rec.get("weightUnit")
        print(f"  item {rid}: weightUnit={wu!r}  (type {type(wu).__name__})")
        if isinstance(wu, dict):
            print(f"      keys: {sorted(wu)}")
        w = rec.get("weight")
        print(f"      weight={w!r}  unitsType={rec.get('unitsType')!r}")

    print()
    print("Whatever shape section 2 prints is what a PATCH must send.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

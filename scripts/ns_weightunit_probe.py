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

import os

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
    # One item per DISTINCT stored value, so every id in the account gets
    # mapped to its unit name -- sampling blindly returns whichever value is
    # most common and leaves the rarer ids (here, id 2 on just 7 items)
    # unidentified, which is precisely the guess that has to be avoided.
    sample = []
    for r in rows:
        raw = r.get("wu")
        if raw is None or str(raw).strip() in ("", "None"):
            continue
        hit = client.suiteql(
            f"SELECT id FROM item WHERE weightunit = '{str(raw).strip()}' "
            f"AND rownum <= 1"
        )
        if hit:
            sample.append(hit[0])
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

    if os.environ.get("WEIGHTUNIT_WRITE_TEST", "").strip().lower() != "true":
        print("\n(set WEIGHTUNIT_WRITE_TEST=true to verify a real PATCH)")
        return 0

    print()
    print("=" * 70)
    print("3. Single-item write test (PATCH, then restore)")
    print("=" * 70)
    # Proving the shape on one item costs ~2 minutes; discovering it was wrong
    # during a full pass costs ~1h45m and writes nothing. Two guesses have
    # already been paid for at that price, so the round trip is worth it.
    target = client.suiteql(
        "SELECT id FROM item WHERE weightunit = '1' AND rownum <= 1"
    )
    if not target:
        print("  no item with weightunit=1 to test against")
        return 0
    rid = str(target[0]["id"])
    before = client.get_record("inventoryItem", rid).get("weightUnit")
    print(f"  item {rid} before: {before!r}")

    try:
        client.update_record("inventoryItem", rid, {"weightUnit": {"id": "2"}})
    except Exception as exc:  # noqa: BLE001 - the whole point is to see this
        print(f"  PATCH REJECTED: {str(exc)[:160]}")
        print(f"      payload: {str(getattr(exc, 'payload', ''))[:300]}")
        return 1

    after = client.get_record("inventoryItem", rid).get("weightUnit")
    print(f"  item {rid} after PATCH {{'id': '2'}}: {after!r}")

    # Put it back the way we found it, so the probe leaves no trace.
    client.update_record("inventoryItem", rid, {"weightUnit": {"id": "1"}})
    restored = client.get_record("inventoryItem", rid).get("weightUnit")
    print(f"  item {rid} restored: {restored!r}")

    ok = isinstance(after, dict) and str(after.get("id")) == "2"
    print(f"\n  RESULT: reference-shape PATCH {'ACCEPTED' if ok else 'DID NOT STICK'}"
          f"; id 2 = {after.get('refName') if isinstance(after, dict) else '?'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

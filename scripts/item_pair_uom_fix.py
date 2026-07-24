"""Detect bottoms (shorts / pants / leggings / ...) and the "Pair(s)" UoM ids.

IMPORTANT -- this does NOT work as an update against EXISTING items. NetSuite
rejects any Units Type change on a saved item with a hard USER_ERROR: "You may
not change the units type of an item after it has been set." (Confirmed live,
run 30106494625; the same rule applies in the UI.) So the 312 existing bottoms
can't be moved off "Each". An item only gets "Pair" at CREATION time.

This module is kept as the reusable BUILDING BLOCKS for the create-time path:
- ``is_bottom(...)`` -- vendor-agnostic bottom detection by the plural garment
  word in the Display Name (plural dodges the "Short Sleeve Tee" false match).
- ``PAIR_BODY`` / the ids -- the account's Pair UoM (Units Type 6, unit 13),
  confirmed by scripts/uom_probe.py, ready to drop into an item-create payload
  (or the CSV create map) so newly-created bottoms are born as Pair.

``plan_body`` / ``main`` remain only to exercise the detector in tests; running
main() live would just collect 400s.
"""

from __future__ import annotations

import os

from concurrent_writes import write_records

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

PAIR_UNITS_TYPE = "6"
PAIR_UNIT = "13"
PAIR_BODY = {
    "unitsType": {"id": PAIR_UNITS_TYPE},
    "stockUnit": {"id": PAIR_UNIT},
    "purchaseUnit": {"id": PAIR_UNIT},
    "saleUnit": {"id": PAIR_UNIT},
}

# Plural garment words that reliably mean "a pair of ___". Plurals dodge the
# classic false positive: "short" is in "Short Sleeve", but "shorts" is not.
BOTTOM_KEYWORDS = (
    "shorts", "pants", "leggings", "tights", "joggers", "sweatpants",
    "capris", "trousers", "boardshorts",
)


def is_bottom(*texts: object) -> bool:
    """True if any provided text (Display Name, item name) names a bottom."""
    blob = " ".join(str(t or "") for t in texts).lower()
    return any(kw in blob for kw in BOTTOM_KEYWORDS)


def plan_body(row: dict) -> dict:
    """Pair UOM for a matched bottom whose Units Type isn't already Pair."""
    if not is_bottom(row.get("displayname"), row.get("itemid")):
        return {}
    if str(row.get("unitstype") or "").strip() == PAIR_UNITS_TYPE:
        return {}  # already Pair
    return dict(PAIR_BODY)


def _projects(client: NetSuiteClient, col: str) -> bool:
    try:
        client.suiteql(f"SELECT {col} FROM item WHERE rownum <= 1")
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    chunk = int(os.environ.get("PAIR_FIX_CHUNK", "40000") or "40000")
    client = NetSuiteClient(cfg.netsuite)

    if not _projects(client, "unitstype"):
        print("unitstype doesn't project on item -- cannot diff; aborting")
        return 1

    max_rows = client.suiteql(
        "SELECT MAX(id) AS m FROM item WHERE matrixtype IN ('PARENT', 'CHILD')"
    )
    max_id = int(max_rows[0]["m"]) if max_rows and max_rows[0].get("m") else 0
    print(f"scanning matrix items up to id {max_id} in chunks of {chunk}")

    considered = written = failures = scanned = matched = 0
    samples = 0
    write_jobs: list[tuple[str, dict]] = []
    for lo in range(0, max_id + 1, chunk):
        hi = lo + chunk - 1
        items = client.suiteql(
            "SELECT id, itemid, displayname, unitstype FROM item "
            f"WHERE matrixtype IN ('PARENT', 'CHILD') AND id BETWEEN {lo} AND {hi}"
        )
        for r in items:
            scanned += 1
            body = plan_body(r)
            if not body:
                continue
            matched += 1
            if max_items and considered >= max_items:
                continue
            considered += 1
            if samples < 15:
                samples += 1
                print(f"  item {r.get('id')} {str(r.get('displayname') or r.get('itemid'))!r}"
                      f" -> Pair")
            if not allow_write:
                written += 1
                continue
            write_jobs.append((str(r["id"]), body))

    _fail_shown = [0]

    def _on_err(rid: str, exc: Exception) -> None:
        _fail_shown[0] += 1
        if _fail_shown[0] <= 10:
            detail = getattr(exc, "payload", "")
            print(f"  FAILED item {rid}: {str(exc)[:120]} :: {str(detail)[:250]}")

    w, f = write_records(client, "inventoryItem", write_jobs, on_error=_on_err)
    written += w
    failures += f

    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(f"\npair uom fix: {verb} {written} item(s); scanned: {scanned:,}; "
          f"bottoms matched: {matched}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

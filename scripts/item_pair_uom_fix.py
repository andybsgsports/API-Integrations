"""Set bottoms (shorts / pants / leggings / ...) to the "Pair(s)" unit instead
of "Each".

Vendor-agnostic: the bottoms in scope span SanMar, Augusta/Momentec, S&S, so
detection is by the garment word in the item's Display Name (falling back to
its name/code), not by any one vendor's feed. Plural forms are used on purpose
-- "shorts" matches "B-Core Shorts" but NOT "Short Sleeve Tee".

Pair ids are the account's UoM setup, confirmed live by scripts/uom_probe.py:
    Units Type "Pair" = 6 ; Pair unit = 13  (vs Each: type 1, unit 1)

One-directional and diff-aware: only flips a matched bottom whose Units Type
isn't already Pair; never touches non-bottoms and never flips Pair back to
Each. Dry-run by default -- the dry run prints what it WOULD flip so the match
list can be eyeballed before any writes. UPDATE_MAX_ITEMS caps writes.
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

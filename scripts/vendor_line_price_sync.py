"""Keep the Preferred Vendor line's Purchase Price equal to the item's cost.

The nightly update writes the dealer cost to the item's own Purchase Price
field (``cost``). The Vendors sublist carries its own Purchase Price per
vendor line, and nothing kept the two together: after a cost change the
Preferred Vendor line still showed the old number (or stayed blank). Andy,
2026-11-18: dealer cost goes in the item's own field AND on the vendor line.

Scope, per run: items whose PREFERRED vendor line belongs to this leg's vendor
(``VENDOR_LINE_SOURCE`` = sanmar / momentec / ss / ua) -- the same ownership
rule the cost itself follows (see pricing_ownership.py). Items whose
Preferred Vendor is anyone else are never touched, and neither is any
non-preferred line: every existing line is sent back as it is, with only the
preferred line's price changed.

Diff-aware: only lines that are blank or differ from cost are written.
Capped per run by ``VENDOR_LINE_MAX_ITEMS`` (default 5,000; lines that
DIFFER go before lines that are blank) and by ``UPDATE_MAX_ITEMS`` when that
is set lower. Honors ``SYNC_DRY_RUN``. Skipped while the price guard is
holding this vendor's prices.
"""

from __future__ import annotations

import os

import price_guard
from concurrent_writes import write_records
from pricing_ownership import (
    VENDOR_MOMENTEC,
    VENDOR_SANMAR,
    VENDOR_SS,
    VENDOR_UA,
)
from run_status import exit_code, leg_deadline, past_deadline

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

VENDOR_IDS = {
    "sanmar": VENDOR_SANMAR,
    "momentec": VENDOR_MOMENTEC,
    "ss": VENDOR_SS,
    "ua": VENDOR_UA,
}
ID_WINDOW = 50_000     # SuiteQL caps one result set at 100k rows
DEFAULT_MAX = 5_000
#: Writes go out in slices this size so the leg deadline is checked between them.
WRITE_SLICE = 500
LABEL = "vendor line price"


def _num(value: object) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _is_preferred(line: dict) -> bool:
    return str(line.get("preferredvendor") or "").upper() in ("T", "TRUE", "1")


def needs_sync(price: object, cost: object) -> bool:
    """True when a preferred line's price is blank or differs from cost."""
    c = _num(cost)
    if c is None or c <= 0:
        return False
    p = _num(price)
    return p is None or abs(p - c) > 0.004


def synced_lines(lines: list[dict], cost: object) -> list[dict] | None:
    """REST ``itemVendor`` lines with the preferred line's price set to cost.

    Returns None when nothing changes. Every line is carried over (vendor,
    code, preferred flag, its own price); only the preferred line's price
    moves.
    """
    c = _num(cost)
    if c is None or c <= 0:
        return None
    out: list[dict] = []
    changed = False
    for line in lines:
        preferred = _is_preferred(line)
        body: dict[str, object] = {
            "vendor": {"id": str(line["vendor"])},
            "preferredVendor": preferred,
        }
        if str(line.get("vendorcode") or "").strip():
            body["vendorCode"] = str(line["vendorcode"])
        price = _num(line.get("purchaseprice"))
        if preferred and needs_sync(line.get("purchaseprice"), c):
            body["purchasePrice"] = c
            changed = True
        elif price is not None:
            body["purchasePrice"] = price
        out.append(body)
    return out if changed else None


def pick(candidates: list[tuple[str, bool]], cap: int) -> list[str]:
    """Item ids to write this run: differing lines first, then blank ones."""
    ordered = [rid for rid, blank in candidates if not blank]
    ordered += [rid for rid, blank in candidates if blank]
    return ordered[:cap] if cap else ordered


def main() -> int:
    source = os.environ.get("VENDOR_LINE_SOURCE", "").strip().lower()
    vendor_id = VENDOR_IDS.get(source)
    if vendor_id is None:
        print(f"{LABEL}: no vendor for VENDOR_LINE_SOURCE={source!r}; nothing to do")
        return 0
    if price_guard.holding():
        print(f"NOTE: {LABEL} ({source}): skipped, prices are held by the price guard")
        return 0
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    cap = int(os.environ.get("VENDOR_LINE_MAX_ITEMS", str(DEFAULT_MAX)) or DEFAULT_MAX)
    run_cap = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    if run_cap and (not cap or run_cap < cap):
        cap = run_cap
    client = NetSuiteClient(cfg.netsuite)

    top_rows = client.suiteql("SELECT MAX(id) AS m FROM item")
    top = int((top_rows[0].get("m") if top_rows else 0) or 0)
    candidates: list[tuple[str, bool]] = []
    cost_by_item: dict[str, object] = {}
    samples: list[str] = []
    for lo in range(0, top + 1, ID_WINDOW):
        for r in client.suiteql(
            "SELECT iv.item, iv.purchaseprice, i.cost FROM itemvendor iv "
            "JOIN item i ON i.id = iv.item "
            f"WHERE i.id > {lo} AND i.id <= {lo + ID_WINDOW} "
            f"AND iv.preferredvendor = 'T' AND iv.vendor = {vendor_id} "
            "AND i.isinactive = 'F' AND i.cost > 0 "
            "AND (iv.purchaseprice IS NULL OR iv.purchaseprice <> i.cost)"
        ):
            if not needs_sync(r.get("purchaseprice"), r.get("cost")):
                continue
            rid = str(r["item"])
            candidates.append((rid, _num(r.get("purchaseprice")) is None))
            if _num(r.get("purchaseprice")) is not None and len(samples) < 5:
                samples.append(f"item {rid}: line {r.get('purchaseprice')} vs "
                               f"cost {r.get('cost')}")
            cost_by_item[rid] = r.get("cost")

    differ = sum(1 for _, blank in candidates if not blank)
    todo = pick(candidates, cap)
    print(f"{LABEL} ({source}): {len(candidates):,} preferred line(s) off from "
          f"cost ({differ:,} differ, {len(candidates) - differ:,} blank); "
          f"{len(todo):,} this run" + (f" (cap {cap:,})" if cap else ""))
    for line in samples:
        # A line that differs night after night is one the write cannot fix
        # (for example a cost with more decimals than the line stores).
        print(f"  differs: {line}")

    jobs: list[tuple[str, dict]] = []
    for i in range(0, len(todo), 250):
        chunk = todo[i : i + 250]
        by_item: dict[str, list[dict]] = {}
        for r in client.suiteql(
            "SELECT item, vendor, vendorcode, preferredvendor, purchaseprice "
            f"FROM itemvendor WHERE item IN ({', '.join(chunk)})"
        ):
            by_item.setdefault(str(r["item"]), []).append(r)
        for rid in chunk:
            lines = synced_lines(by_item.get(rid, []), cost_by_item.get(rid))
            if lines:
                jobs.append((rid, {"itemVendor": {"items": lines}}))

    if not allow_write:
        print(f"{LABEL} ({source}): WOULD write (dry run) {len(jobs):,} item(s); "
              f"failures: 0")
        return 0
    shown = [0]
    other = [0]

    def _on_err(rid: str, exc: Exception) -> None:
        shown[0] += 1
        if "429" not in str(exc):
            other[0] += 1
        if shown[0] <= 10:
            print(f"  FAILED item {rid}: {str(exc)[:150]} "
                  f":: {str(getattr(exc, 'payload', ''))[:200]}")

    deadline = leg_deadline()
    written = failures = 0
    for i in range(0, len(jobs), WRITE_SLICE):
        if past_deadline(deadline):
            print(f"NOTE: {LABEL} ({source}): stopped at the leg time budget with "
                  f"{len(jobs) - i:,} of {len(jobs):,} line(s) not reached; the "
                  f"next run carries on from the lines still off.")
            break
        w, f = write_records(client, "inventoryItem", jobs[i : i + WRITE_SLICE],
                             on_error=_on_err)
        written += w
        failures += f
    print(f"{LABEL} ({source}): wrote {written:,} item(s); failures: {failures:,}")
    return exit_code(f"{LABEL} ({source})", failures, other[0], written + failures)


if __name__ == "__main__":
    raise SystemExit(main())

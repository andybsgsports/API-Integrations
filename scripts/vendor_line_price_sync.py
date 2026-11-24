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
from concurrent.futures import ThreadPoolExecutor

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
#: A line that already has a price keeps it in a nested price line, and the
#: REST record API accepts a PATCH of purchasePrice and ignores it (94 Under
#: Armour lines unchanged run after run; sandbox runs 44668710929 and
#: 44669765243, 2026-11-20). Those lines go to the matrix RESTlet's
#: "vendorPrices" action instead, this many items per call.
RESTLET_BATCH = 25
RESTLET_WORKERS = 3
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


def restlet_prices(client, ns_cfg, priced: list[dict], deadline: float | None,
                   label: str) -> tuple[int, int, int, int]:
    """Send already-priced lines to the RESTlet. Returns
    ``(written, failed, failed_not_throttling, not_attempted)``."""
    if not priced:
        return 0, 0, 0, 0
    script_id = getattr(ns_cfg, "matrix_script_id", "")
    deploy_id = getattr(ns_cfg, "matrix_deploy_id", "")
    if not script_id or not deploy_id:
        print(f"NOTE: {label}: {len(priced):,} line(s) already have a price and "
              f"need the matrix RESTlet, which is not configured; not attempted.")
        return 0, 0, 0, len(priced)
    batches = [priced[i : i + RESTLET_BATCH] for i in range(0, len(priced), RESTLET_BATCH)]
    # One call first: an older copy of the RESTlet answers without the action.
    first = client.call_restlet(script_id, deploy_id,
                                {"action": "vendorPrices", "vendorPrices": batches[0]})
    if first.get("action") != "vendorPrices":
        print(f"NOTE: {label}: {len(priced):,} line(s) already have a price, and "
              f"the RESTlet in this account does not have the vendorPrices action "
              f"yet (push suitescript/bsg_sanmar_matrix.js); not attempted.")
        return 0, 0, 0, len(priced)
    written = failed = other = skipped = 0
    shown = [0]

    def _tally(results: list[dict]) -> None:
        nonlocal written, failed, other
        for r in results:
            if r.get("status") in ("updated", "unchanged"):
                written += 1
            else:
                failed += 1
                other += 1
                shown[0] += 1
                if shown[0] <= 10:
                    print(f"  FAILED item {r.get('id')}: {str(r.get('message'))[:200]}")

    def _call(batch: list[dict]) -> list[dict]:
        resp = client.call_restlet(script_id, deploy_id,
                                   {"action": "vendorPrices", "vendorPrices": batch})
        return resp.get("results") or []

    _tally(first.get("results") or [])
    rest = batches[1:]
    for i in range(0, len(rest), RESTLET_WORKERS * 4):
        if past_deadline(deadline):
            skipped = sum(len(b) for b in rest[i:])
            print(f"NOTE: {label}: stopped at the leg time budget with {skipped:,} "
                  f"already-priced line(s) not reached.")
            break
        group = rest[i : i + RESTLET_WORKERS * 4]
        with ThreadPoolExecutor(max_workers=RESTLET_WORKERS) as ex:
            futs = {ex.submit(_call, b): b for b in group}
            for fut, batch in futs.items():
                try:
                    _tally(fut.result())
                except Exception as exc:  # noqa: BLE001 - one batch, keep going
                    failed += len(batch)
                    if "429" not in str(exc):
                        other += len(batch)
                    shown[0] += 1
                    if shown[0] <= 10:
                        print(f"  FAILED batch of {len(batch)}: {str(exc)[:200]}")
    return written, failed, other, skipped


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
    priced: list[dict] = []   # lines that already have a price: RESTlet only
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
            if not lines:
                continue
            had_price = any(
                _is_preferred(r) and _num(r.get("purchaseprice")) is not None
                for r in by_item.get(rid, []))
            if had_price:
                priced.append({"id": int(rid), "vendor": int(vendor_id),
                               "price": _num(cost_by_item.get(rid))})
            else:
                jobs.append((rid, {"itemVendor": {"items": lines}}))

    if not allow_write:
        print(f"{LABEL} ({source}): WOULD write (dry run) "
              f"{len(jobs) + len(priced):,} item(s) ({len(priced):,} through the "
              f"RESTlet); failures: 0")
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
    r_written, r_failed, r_other, r_skipped = restlet_prices(
        client, cfg.netsuite, priced, deadline, f"{LABEL} ({source})")
    written += r_written
    failures += r_failed
    other[0] += r_other
    print(f"{LABEL} ({source}): wrote {written:,} item(s) ({r_written:,} through "
          f"the RESTlet); failures: {failures:,}"
          + (f"; not attempted: {r_skipped:,}" if r_skipped else ""))
    check = [str(p["id"]) for p in priced[:100]] + [rid for rid, _ in jobs[:100]]
    still = 0
    for i in range(0, len(check), 100):
        for r in client.suiteql(
            "SELECT iv.item, iv.purchaseprice, i.cost FROM itemvendor iv "
            "JOIN item i ON i.id = iv.item "
            f"WHERE iv.item IN ({', '.join(check[i : i + 100])}) "
            f"AND iv.preferredvendor = 'T' AND iv.vendor = {vendor_id}"
        ):
            if needs_sync(r.get("purchaseprice"), r.get("cost")):
                still += 1
    if check:
        print(f"{LABEL} ({source}): read back {len(check):,} written item(s); "
              f"{still:,} still differ from cost")
    return exit_code(f"{LABEL} ({source})", failures, other[0], written + failures)


if __name__ == "__main__":
    raise SystemExit(main())

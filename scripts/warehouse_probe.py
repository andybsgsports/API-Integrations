"""Read-only probe: what warehouse codes actually appear in the sandbox's
per-warehouse availability text fields? (Runs on CI.)

SanMar's warehouse set is already a fixed, documented 9-location table
(``sanmar.constants.WAREHOUSES``); this just confirms nothing unexpected
shows up. S&S has no such static table in this codebase -- the code reads
whatever ``warehouseAbbr`` values the API returns -- so this is the only
source of truth for "how many separate fields do we need." Reads the real
per-warehouse lines already written by the full backfill runs
(``custitem_sanmar_qty_by_whse`` / ``custitem_ss_qty_by_whse``, one
``CODE: qty`` line per warehouse) and reports the distinct code set with
item counts, ahead of designing one custom field per warehouse.
"""

from __future__ import annotations

import os
import re

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.sanmar import constants as C
from ss_activewear_netsuite.config import get_config as ss_config
from ss_activewear_netsuite.ss_activewear.client import SsClient

LINE_RE = re.compile(r"^\s*([^:\n]+?)\s*:\s*([\d,]+)\s*$", re.MULTILINE)


def collect(client: NetSuiteClient, field: str, label: str, sibling: str) -> None:
    print(f"\n=== {label} ({field}) ===")
    # Sanity check: how many rows carry ANY value for this supplier at all
    # (a field known-populated by the full run) vs. this specific field --
    # narrows down "field never got written" vs. "parsing is wrong".
    sib_rows = client.suiteql(f"SELECT COUNT(*) AS n FROM item WHERE {sibling} IS NOT NULL")
    print(f"sibling field {sibling} non-null count: {sib_rows[0]['n'] if sib_rows else '?'}")

    codes: dict[str, int] = {}
    items_with_data = 0
    shown = 0
    # Well under the ~100k SuiteQL result-window cap (a few thousand rows at
    # most here), so a single call is fine -- the client pages internally.
    rows = client.suiteql(f"SELECT {field} AS whse FROM item WHERE {field} IS NOT NULL")
    for r in rows:
        text = str(r.get("whse") or "")
        if not text.strip():
            continue
        items_with_data += 1
        if shown < 3:
            print(f"  sample raw value: {text!r}")
            shown += 1
        for code, _qty in LINE_RE.findall(text):
            codes[code] = codes.get(code, 0) + 1
    print(f"items with data: {items_with_data:,}")
    print(f"distinct warehouse codes: {len(codes)}")
    for code, n in sorted(codes.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {code!r:<28} appears on {n:,} items")


def probe_ss_live(client: NetSuiteClient, sample_size: int = 150) -> None:
    """The filtered /Products?styleid= batch endpoint (what ss_backfill.py uses
    for everything else) never returns per-warehouse detail -- confirmed
    empirically, aggregate qty is real but warehouses=() every time. The
    per-SKU /Inventory/{sku} endpoint DOES carry it, under the key
    "warehouses" (not "warehouseAvailability" like /Products -- client.py's
    parser has been fixed to accept either).

    S&S's own network is mid-consolidation this year (several DCs closing in
    2026 per their public announcements), so this deliberately does NOT
    hardcode a warehouse table the way SanMar's constants.py does. Instead it
    samples real /Inventory/{sku} responses across a broad, diverse slice of
    our actually-matched S&S items (not arbitrary catalog styles) to build an
    empirical superset of the codes our field set needs to cover.
    """
    print(f"\n=== S&S live API check (per-SKU /Inventory/{{sku}} endpoint, "
          f"sampling up to {sample_size} matched items) ===")
    rows = client.suiteql(
        "SELECT custitem_ss_sku AS sku FROM item "
        "WHERE custitem_ss_sku IS NOT NULL ORDER BY id"
    )
    skus = sorted({str(r["sku"]) for r in rows if r.get("sku")})
    # Evenly spread the sample across the full id range rather than the first
    # N rows, so it isn't biased toward one supplier batch/style run.
    step = max(1, len(skus) // sample_size)
    sample = skus[::step][:sample_size]
    print(f"matched S&S items: {len(skus):,}; sampling {len(sample)}")

    ss = SsClient(ss_config().ss_api)
    codes: dict[str, int] = {}
    errors = 0
    for sku in sample:
        try:
            inv = ss.get_inventory(sku)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            if errors <= 5:
                print(f"  sku={sku}: ERROR {str(exc)[:120]}")
            continue
        if inv is None:
            continue
        for w in inv.warehouses:
            codes[w.warehouse_abbr] = codes.get(w.warehouse_abbr, 0) + 1
    print(f"errors: {errors}")
    print(f"distinct warehouse codes seen: {len(codes)}")
    for code, n in sorted(codes.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {code!r:<10} appears on {n:,}/{len(sample)} sampled items")


def main() -> int:
    client = NetSuiteClient(get_config().netsuite)

    print("=== SanMar known warehouse table (sanmar.constants.WAREHOUSES) ===")
    for no, label in C.WAREHOUSES.items():
        print(f"  {no:>3}  {label}")

    collect(client, "custitem_sanmar_qty_by_whse", "SanMar sandbox data", "custitem_sanmar_style")
    collect(client, "custitem_ss_qty_by_whse", "S&S sandbox data", "custitem_ss_sku")
    collect(client, "custitem_mtec_qty_by_whse", "Momentec sandbox data", "custitem_mtec_item_sku")
    collect(client, "custitem_ua_qty_by_whse", "UA sandbox data", "custitem_ua_part_id")
    if (os.environ.get("SS_LIVE_PROBE") or "").lower() == "true":
        probe_ss_live(client)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

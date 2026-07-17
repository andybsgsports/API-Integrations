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


def probe_ss_live() -> None:
    """Hit the S&S API directly (the same filtered batch endpoint ss_backfill.py
    uses) for a handful of styles and print the raw warehouseAvailability the
    API actually returns -- resolves whether "0 items with data" in the
    sandbox means the write is broken, or the feed itself has no per-warehouse
    breakdown on that endpoint."""
    print("\n=== S&S live API check (filtered /Products?styleid= endpoint) ===")
    ss = SsClient(ss_config().ss_api)
    styles = []
    for s in ss.iter_styles():
        if s.style_id:
            styles.append(s.style_id)
        if len(styles) >= 3:
            break
    print(f"probing styleIDs: {styles}")
    skus = []
    seen = 0
    for p in ss._products_for_styles(styles):
        if seen >= 5:
            break
        seen += 1
        skus.append(p.sku)
        print(f"  sku={p.sku} qty_available={p.qty_available} warehouses={p.warehouses!r}")

    print("\n=== S&S live API check (per-SKU /Inventory/{sku} endpoint, RAW) ===")
    for sku in skus[:2]:
        raw = ss._get(f"/Inventory/{sku}")
        print(f"  sku={sku} raw={raw!r}")


def main() -> int:
    client = NetSuiteClient(get_config().netsuite)

    print("=== SanMar known warehouse table (sanmar.constants.WAREHOUSES) ===")
    for no, label in C.WAREHOUSES.items():
        print(f"  {no:>3}  {label}")

    collect(client, "custitem_sanmar_qty_by_whse", "SanMar sandbox data", "custitem_sanmar_style")
    collect(client, "custitem_ss_qty_by_whse", "S&S sandbox data", "custitem_ss_sku")
    probe_ss_live()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

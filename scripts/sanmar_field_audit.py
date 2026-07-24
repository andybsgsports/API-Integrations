"""Read-only coverage audit: which SanMar fields are ACTUALLY populated in NetSuite.

Every writer prints a summary line ("store name/desc updated: 45531"), but that
counts what the run *attempted*, not what NetSuite kept. When a single field is
rejected NetSuite discards the entire record PATCH, so a run can report tens of
thousands of updates and persist none of them -- exactly what the 21-char
Stock Description bug did between 2026-07-22 and 2026-07-24 (45,531 attempted,
45,531 rejected, 0 written, across five consecutive "completed" runs).

This asks NetSuite directly instead: for SanMar-keyed items, what fraction of
them carry each field? Gaps show up as a low fill rate on a specific field
rather than as a wall of identical PATCH errors. Writes nothing -- safe to run
at any time, including against production.

Scope: items carrying the SanMar identity key (``custitem_sanmar_unique_key``).
Self-enabling fields (On Sale, Closeout) and native store columns are probed
first and skipped when NetSuite doesn't expose them, so this never fails just
because a field hasn't been created yet.
"""

from __future__ import annotations

import os

from warehouse_fields import SANMAR_QTY_FIELDS

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

# Identifies an item as SanMar's. Everything is measured against this population.
SCOPE = "custitem_sanmar_unique_key IS NOT NULL"

# (column, label) in report order. Checkboxes are handled separately because
# "populated" is meaningless for them -- we count how many are checked.
TEXT_FIELDS: list[tuple[str, str]] = [
    ("custitem_sanmar_style", "SanMar Style"),
    ("custitem_sanmar_mf_color", "Mainframe Color"),
    ("custitem_sanmar_gtin", "SanMar GTIN"),
    ("custitem_sanmar_size_index", "Size Index"),
    ("custitem_sanmar_inventory_key", "Inventory Key"),
    ("custitem_sanmar_status", "Product Status"),
    ("custitem_sanmar_qty_available", "Qty Available (total)"),
    ("custitem_sanmar_map", "MAP Price"),
    ("custitem_sanmar_msrp", "MSRP"),
    ("custitem_sanmar_case_price", "Case Price"),
    ("custitem_sanmar_case_size", "Case Size"),
    ("custitem_sanmar_front_image_url", "Front Image URL"),
    ("upccode", "UPC Code"),
    ("cost", "Purchase Price (cost)"),
    ("weight", "Weight"),
    ("weightunit", "Weight Unit"),
    ("manufacturer", "Manufacturer"),
    ("displayname", "Display Name"),
    ("salesdescription", "Sales Description"),
    ("storedisplayname", "Store Display Name"),
    ("storedescription", "Store Description"),
    ("stockdescription", "Stock Description"),
    ("custitem_feed_source", "Feed Source (heartbeat)"),
    ("custitem_feed_last_seen", "Feed Last Seen (heartbeat)"),
]

CHECKBOX_FIELDS: list[tuple[str, str]] = [
    ("custitem_bsg_on_sale", "On Sale (checked)"),
    ("custitem_sanmar_is_closeout", "Closeout (checked)"),
]

# Fill rate below this is called out as a gap worth investigating.
LOW_WATER = float(os.environ.get("AUDIT_LOW_WATER", "90"))


def _projects(client: NetSuiteClient, column: str) -> bool:
    """True when NetSuite will return this column (field exists and is queryable)."""
    try:
        client.suiteql(f"SELECT {column} FROM item WHERE rownum <= 1")
        return True
    except Exception:  # noqa: BLE001 - a missing field is an expected outcome here
        return False


def _counts(
    client: NetSuiteClient, fields: list[tuple[str, str]], checkbox: bool
) -> list[tuple[str, str, int]]:
    """Populated (or checked) count per field, in one aggregate query.

    Aliased f0..fN because Oracle caps identifiers at 30 chars and several
    scriptids are longer than that.
    """
    if not fields:
        return []
    parts = []
    for i, (col, _) in enumerate(fields):
        test = f"{col} = 'T'" if checkbox else f"{col} IS NOT NULL"
        parts.append(f"SUM(CASE WHEN {test} THEN 1 ELSE 0 END) AS f{i}")
    rows = client.suiteql(
        f"SELECT COUNT(*) AS total, {', '.join(parts)} FROM item WHERE {SCOPE}"
    )
    row = {str(k).lower(): v for k, v in rows[0].items()}
    return [
        (col, label, int(row.get(f"f{i}") or 0))
        for i, (col, label) in enumerate(fields)
    ]


def main() -> int:
    cfg = get_config()
    client = NetSuiteClient(cfg.netsuite)

    total = int(client.suiteql(f"SELECT COUNT(*) AS n FROM item WHERE {SCOPE}")[0]["n"])
    print(f"SanMar-keyed items in NetSuite: {total:,}")
    if not total:
        print("No SanMar-keyed items found -- nothing to audit.")
        return 0

    text = [(c, lbl) for c, lbl in TEXT_FIELDS if _projects(client, c)]
    boxes = [(c, lbl) for c, lbl in CHECKBOX_FIELDS if _projects(client, c)]
    whse = [(c, c.replace("custitem_sanmar_qty_", "").title())
            for c in SANMAR_QTY_FIELDS if _projects(client, c)]

    skipped = [lbl for c, lbl in TEXT_FIELDS + CHECKBOX_FIELDS
               if c not in {x[0] for x in text + boxes}]
    if skipped:
        print(f"not queryable (skipped): {', '.join(skipped)}")

    results = _counts(client, text, checkbox=False)
    box_results = _counts(client, boxes, checkbox=True)
    whse_results = _counts(client, whse, checkbox=False)

    width = max(len(lbl) for _, lbl, _ in results + box_results + whse_results) + 1

    def show(rows: list[tuple[str, str, int]], header: str, gap_check: bool) -> list[str]:
        print(f"\n{header}")
        print("-" * (width + 26))
        gaps = []
        for _, label, n in sorted(rows, key=lambda r: r[2]):
            pct = 100.0 * n / total
            flag = ""
            if gap_check and pct < LOW_WATER:
                flag = "  <-- GAP"
                gaps.append(f"{label} ({pct:.1f}%)")
            print(f"{label:<{width}} {n:>9,} / {total:,}  {pct:5.1f}%{flag}")
        return gaps

    gaps = show(results, "Field coverage (populated / total)", gap_check=True)
    show(box_results, "Checkboxes (how many are checked -- low is not a gap)",
         gap_check=False)
    show(whse_results, "Per-warehouse inventory (blank = no stock reported)",
         gap_check=False)

    print("\nBase Price lives in the item pricing sublist, not on the item record -- "
          "it is not covered here.")
    if gaps:
        print(f"\n{len(gaps)} field(s) below {LOW_WATER:.0f}% coverage:")
        for g in gaps:
            print(f"  - {g}")
    else:
        print(f"\nAll audited fields at or above {LOW_WATER:.0f}% coverage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

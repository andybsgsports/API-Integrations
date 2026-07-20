"""Champro data from the manually-downloaded champrosports.com export
(data/champro_products.csv), matched to NetSuite items by vendor code (SKU).

Fills the stable attributes the live DC OneSource feed misses -- UPC/GTIN,
weight, Champro keys, Manufacturer, and the sizing-guide / fabrics reference
links -- for items whose grouped or no-UPC SKU the feed can't reach (e.g. the
Youth pitcher's rubber B041, sold on the site as the grouped "B040-B041").

Inventory is deliberately excluded: the CSV is a manual download, so live qty
stays with the DC OneSource feed and never goes stale from this file. Match is
guarded so a vendor-code collision can't clobber a SanMar/S&S/Momentec/UA item.
Diff-aware; honors SYNC_DRY_RUN; UPDATE_MAX_ITEMS caps writes.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from dcos_backfill import champro_doc_urls

from sanmar_netsuite.config import get_config as ns_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.feed_seen import FIELDS as SEEN_FIELDS
from sanmar_netsuite.netsuite.feed_seen import stamp
from sanmar_netsuite.netsuite.repository import _sql_escape

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "champro_products.csv"
LABEL = "Champro"

# Champro custom fields this backfill sets (no qty -- inventory excluded).
FIELDS = [
    "custitem_champro_part_id",
    "custitem_champro_style",
    "custitem_champro_gtin",
    "custitem_champro_size_guide",
    "custitem_champro_fabrics",
]
# Other suppliers' key fields -- if any is set (and champro isn't), the item
# belongs to that supplier; skip it so a shared vendor code can't clobber it.
OTHER_KEYS = [
    "custitem_sanmar_unique_key",
    "custitem_ss_sku",
    "custitem_mtec_item_sku",
    "custitem_ua_part_id",
]


def _num(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _same(current: object, new: object) -> bool:
    cs = ("" if current is None else str(current)).strip()
    ns_ = str(new).strip()
    if cs == ns_:
        return True
    try:
        return float(cs) == float(ns_)
    except ValueError:
        return False


def load_csv() -> dict[str, dict]:
    out: dict[str, dict] = {}
    with CSV_PATH.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            sku = (row.get("sku") or "").strip()
            if sku:
                out[sku.upper()] = row
    return out


def main() -> int:
    allow_write = not ns_config().sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    client = NetSuiteClient(ns_config().netsuite)

    by_sku = load_csv()
    print(f"champro product CSV: {len(by_sku):,} SKUs")
    docs = champro_doc_urls(client, ns_config().netsuite.account_id)
    if docs:
        print(f"champro reference-doc links: {sorted(docs)}")

    skus = sorted(by_sku)
    cols = ", ".join(FIELDS + OTHER_KEYS + SEEN_FIELDS)
    matched = considered = written = unchanged = upc_filled = skipped = failures = 0
    for i in range(0, len(skus), 300):
        in_list = ", ".join(f"'{_sql_escape(s)}'" for s in skus[i : i + 300])
        rows = client.suiteql(
            "SELECT id, vendorname, upccode, weight, manufacturer, "
            f"custitem_ss_brand, {cols} "
            f"FROM item WHERE UPPER(vendorname) IN ({in_list})"
        )
        for row in rows:
            rec = by_sku.get(str(row.get("vendorname") or "").strip().upper())
            if not rec:
                continue
            is_champro = str(row.get("custitem_champro_part_id") or "").strip()
            if not is_champro and any(str(row.get(k) or "").strip() for k in OTHER_KEYS):
                skipped += 1  # belongs to another supplier; don't clobber
                continue
            matched += 1
            upc = (rec.get("upc") or "").strip()

            # Champro keys: fill only where empty, so DC OneSource stays
            # authoritative on the items it already matched (its partId can
            # differ from the SKU). The CSV fills the gaps it misses.
            want: dict[str, object] = {}
            if not is_champro:
                want["custitem_champro_part_id"] = rec.get("sku")
            if not str(row.get("custitem_champro_style") or "").strip():
                want["custitem_champro_style"] = rec.get("sku")
            if upc and not str(row.get("custitem_champro_gtin") or "").strip():
                want["custitem_champro_gtin"] = upc
            want.update(docs)  # doc links (same URLs DC OneSource uses; diff-aware)
            want = {k: v for k, v in want.items() if v not in (None, "")}
            body = {f: v for f, v in want.items() if not _same(row.get(f), v)}

            if upc and not str(row.get("upccode") or "").strip():
                body["upcCode"] = upc
            wt = _num(rec.get("weight"))
            if wt is not None and not _same(row.get("weight"), wt):
                body["weight"] = wt
            if (not str(row.get("custitem_ss_brand") or "").strip()
                    and not _same(row.get("manufacturer"), LABEL)):
                body["manufacturer"] = LABEL
            stamp(body, row, "champro")

            if not body:
                unchanged += 1
                continue
            if max_items and considered >= max_items:
                continue
            considered += 1
            if "upcCode" in body:
                upc_filled += 1
            if not allow_write:
                written += 1
                continue
            try:
                client.update_record("inventoryItem", str(row["id"]), body)
                written += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if failures <= 10:
                    print(f"  FAILED item {row['id']}: {str(exc)[:150]}")

    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(f"\nchampro CSV backfill: matched {matched} item(s); {verb} {written}; "
          f"unchanged: {unchanged}; upcCode filled: {upc_filled}; "
          f"skipped (other supplier): {skipped}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

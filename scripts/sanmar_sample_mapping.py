"""Read-only sample: exactly what we pull from the SanMar feed and where each
value lands in NetSuite -- so the mapping can be reviewed before trusting a run.

Deliberately calls the SAME ``build_payloads()`` the live writer uses, rather
than re-deriving anything. Whatever this prints is literally what the updater
would write, so a mistake visible here is a mistake in the real sync (and a
mapping that looks right here is the one that runs).

Samples one SKU from each of the first N styles rather than N sizes of a single
style, so the review covers different garments, price points and status values
instead of twenty-five near-identical rows.

Writes two files under ``data/``:

* ``sanmar_sample_items.csv``  -- one row per sampled SKU, one column per
  NetSuite field (open in a spreadsheet to eyeball values).
* ``sanmar_sample_mapping.csv`` -- the legend: NetSuite field, what it means,
  and an example value.

Touches NetSuite not at all -- it only reads SanMar's SFTP feed. ``SAMPLE_SIZE``
overrides the default of 25.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from field_descriptions import DESCRIPTIONS
from sanmar_field_update import CLOSEOUT_FIELD, ON_SALE_FIELD, _dl, build_payloads

from sanmar_netsuite.config import get_config
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_inventory, parse_styles

ROOT = Path(__file__).resolve().parents[1]

# Native (non-custitem) NetSuite fields the updater writes, with plain-English
# meaning -- DESCRIPTIONS only covers custitem_* fields.
NATIVE_LABELS: dict[str, str] = {
    "price": "Base Price - the sale price when on sale, else regular price.",
    "cost": "Purchase Price - what BSG pays SanMar for one unit.",
    "weight": "Shipping weight of one unit.",
    "weightUnit": "Unit the weight is expressed in (lb / oz).",
    "storeDisplayName": "Product name shown in the web store.",
    "storeDescription": "Marketing copy shown in the web store.",
    "upccode": "UPC / barcode - the key we match SanMar SKUs to NetSuite items on.",
    "manufacturer": "Brand that makes the garment (Port Authority, Gildan, ...).",
}


def _describe(field: str) -> str:
    if field in NATIVE_LABELS:
        return NATIVE_LABELS[field]
    desc = DESCRIPTIONS.get(field, "")
    return desc or "(no description registered)"


def main() -> int:
    sample_size = int(os.environ.get("SAMPLE_SIZE", "25") or "25")
    cfg = get_config()

    styles = parse_styles(_dl(cfg, C.FILE_SDL_N))
    inventory = parse_inventory(_dl(cfg, C.FILE_DIP))
    # Assume both self-enabling flags are live so the sample shows them; this
    # script never writes, so claiming them here is free.
    payloads, natives, store_by_gtin, closeout_by_gtin = build_payloads(
        styles, inventory, on_sale_field=ON_SALE_FIELD
    )
    print(f"feed parsed: {len(styles):,} styles, {len(payloads):,} SKUs with a GTIN")

    # One SKU per style, so the sample spans different garments.
    picked: list[tuple[str, object]] = []
    for style in styles:
        for sku in style.skus:
            if sku.gtin and sku.gtin in payloads:
                picked.append((sku.gtin, style))
                break
        if len(picked) >= sample_size:
            break
    print(f"sampled {len(picked)} SKU(s), one per style")

    rows: list[dict[str, object]] = []
    for gtin, style in picked:
        entry = dict(payloads[gtin])
        price, cost, weight, weight_unit = natives.get(gtin, (None, None, None, None))
        disp, sdesc = store_by_gtin.get(gtin, ("", ""))
        entry["upccode"] = gtin
        if price is not None:
            entry["price"] = price
        if cost is not None:
            entry["cost"] = cost
        if weight is not None:
            entry["weight"] = weight
        if weight_unit:
            entry["weightUnit"] = weight_unit
        if disp:
            entry["storeDisplayName"] = disp
        if sdesc:
            entry["storeDescription"] = sdesc
        entry[CLOSEOUT_FIELD] = closeout_by_gtin.get(gtin, False)
        entry["_style_title"] = getattr(style, "title", "")
        rows.append(entry)

    # Stable column order: identity first, then everything else alphabetically.
    lead = ["upccode", "_style_title", "custitem_sanmar_style",
            "custitem_sanmar_mf_color", "custitem_sanmar_size_index"]
    every = sorted({k for r in rows for k in r})
    columns = [c for c in lead if c in every] + [c for c in every if c not in lead]

    data = ROOT / "data"
    data.mkdir(parents=True, exist_ok=True)

    items_path = data / "sanmar_sample_items.csv"
    with items_path.open("w", encoding="utf-8", newline="") as fh:
        dw = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        dw.writeheader()
        for r in rows:
            dw.writerow(r)

    map_path = data / "sanmar_sample_mapping.csv"
    with map_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["netsuite_field", "meaning", "example_value", "populated_in_sample"])
        for col in columns:
            if col == "_style_title":
                continue
            example = next((str(r[col]) for r in rows if r.get(col) not in (None, "")), "")
            filled = sum(1 for r in rows if r.get(col) not in (None, ""))
            w.writerow([col, _describe(col), example[:120], f"{filled}/{len(rows)}"])

    # Readable breakdown of the first item, for a quick sanity read in the log.
    if rows:
        first = rows[0]
        print(f"\nSample item 1 of {len(rows)} "
              f"(style {first.get('custitem_sanmar_style', '?')} / "
              f"{first.get('custitem_sanmar_mf_color', '?')}):")
        print("-" * 78)
        for col in columns:
            if col == "_style_title":
                continue
            val = first.get(col, "")
            if val in (None, ""):
                continue
            print(f"  {col:<34} = {str(val)[:60]}")

    print(f"\nwrote {items_path.relative_to(ROOT)} ({len(rows)} rows x "
          f"{len(columns)} columns)")
    print(f"wrote {map_path.relative_to(ROOT)} (field-by-field legend)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

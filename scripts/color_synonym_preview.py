"""Read-only preview of the vendor color-synonym correction (Momentec).

Momentec matches feed SKUs to items purely on colour+size option ids (no GTIN
fallback), so when the feed's colour name is a different *word* for our value --
a vendor's "Collegiate Blue" vs our "Columbia Blue" -- the SKU never matches and
the item gets no data. ``data/color_synonyms.csv`` is a reviewed
feed_color -> ns_color bridge that lets those SKUs match.

This preview shows the blast radius before any write: which items the synonym
bridge newly matches, and -- since Andy chose to display the VENDOR's names --
what each matched item's colour would change FROM (our stored value) TO (the
feed's vendor name). It scopes automatically to items an actual Momentec feed
SKU matched, so a Columbia Blue item that ISN'T a vendor "Collegiate Blue" is
never touched. No writes.
"""

from __future__ import annotations

import csv
from pathlib import Path

from momentec_backfill import fetch

from momentec_netsuite.adopt import clean_color, match_momentec
from momentec_netsuite.config import get_config
from momentec_netsuite.feeds import parse_product_data
from sanmar_netsuite.config import get_config as ns_config
from sanmar_netsuite.netsuite.adopt import OptionMaps
from sanmar_netsuite.netsuite.client import NetSuiteClient

ROOT = Path(__file__).resolve().parents[1]


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def load_synonyms() -> dict[str, str]:
    out: dict[str, str] = {}
    path = ROOT / "data" / "color_synonyms.csv"
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            fc, nc = _norm(r.get("feed_color", "")), _norm(r.get("ns_color", ""))
            if fc and nc:
                out[fc] = nc
    return out


def main() -> int:
    cfg = get_config()
    dl = Path(cfg.download_dir)
    synonyms = load_synonyms()
    print(f"synonyms loaded: {len(synonyms)} -> {sorted(synonyms.items())}")

    styles = parse_product_data([
        fetch(cfg.products_url, dl / "product-data-std-all.csv"),
        fetch(cfg.sublimation_url, dl / "sublimation-product-data-std-all.csv"),
    ])
    client = NetSuiteClient(ns_config().netsuite)

    # Baseline (no synonyms) vs bridged, to isolate what the synonyms unlock.
    base = match_momentec(client, styles)
    bridged = match_momentec(client, styles, synonyms=synonyms)
    print(f"feed SKUs matched -- baseline: {len(base.matched):,}; "
          f"with synonyms: {len(bridged.matched):,} "
          f"(+{len(bridged.matched) - len(base.matched):,} unlocked)")

    options = OptionMaps(client)  # for matched_color_id -> current colour name

    # Correction candidates: matched items whose stored colour NAME differs from
    # the feed's vendor colour name -> would be renamed to the vendor name.
    cands = []
    for r in bridged.matched:
        if not r.matched_color_id:
            continue
        cur_name = options.color_info.get(r.matched_color_id, ("", ""))[0]
        vendor_name = clean_color(r.color)
        if _norm(cur_name) and _norm(cur_name) != _norm(vendor_name):
            cands.append({
                "item_id": r.ns_id, "style": r.style, "size": r.size,
                "current_color": cur_name, "vendor_color": vendor_name,
                "gtin": r.gtin, "method": r.method,
            })

    # Attach itemids for readability (small set).
    ids = sorted({c["item_id"] for c in cands})
    itemid_by_id: dict[str, str] = {}
    for i in range(0, len(ids), 200):
        chunk = ids[i : i + 200]
        in_list = ", ".join(str(int(x)) for x in chunk) or "0"
        for row in client.suiteql(
            f"SELECT id, itemid FROM item WHERE id IN ({in_list})"
        ):
            itemid_by_id[str(row["id"])] = str(row.get("itemid") or "")
    for c in cands:
        c["itemid"] = itemid_by_id.get(c["item_id"], "")

    out = ROOT / "data" / "color_synonym_preview.csv"
    cols = ["item_id", "itemid", "style", "size", "current_color",
            "vendor_color", "gtin", "method"]
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(sorted(cands, key=lambda c: (c["current_color"], c["itemid"])))

    by_pair: dict[tuple[str, str], int] = {}
    for c in cands:
        key = (c["current_color"], c["vendor_color"])
        by_pair[key] = by_pair.get(key, 0) + 1
    print(f"\ncolour-correction candidates (would rename to vendor name): {len(cands):,}")
    for (cur, ven), n in sorted(by_pair.items(), key=lambda kv: -kv[1]):
        print(f"  {cur!r} -> {ven!r}: {n} item(s)")
    for c in sorted(cands, key=lambda c: c["itemid"])[:12]:
        print(f"    sample: {c['itemid']} (id {c['item_id']}) "
              f"{c['current_color']!r} -> {c['vendor_color']!r} [{c['method']}]")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

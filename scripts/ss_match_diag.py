"""Explain why one NetSuite item didn't get S&S data.

The S&S back-fill matches an item to an S&S SKU two ways: (1) GTIN == the item's
upcCode, or (2) the item's Vendor Name/Code == an S&S style name, then by
color/size option. This prints, for a given item, its vendorname/upcCode and the
S&S catalog rows for the matching color+size, so we can see which join (if any)
should have fired and why it didn't.

Env: ITEM_ID (default 122), SS_STYLE_ID (default 6124 = 695HBM),
MATCH_COLOR (default Black), MATCH_SIZES (comma list, default "L,Large").
Read-only.
"""

from __future__ import annotations

import os

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from ss_activewear_netsuite.config import get_config as ss_config
from ss_activewear_netsuite.ss_activewear.client import SsClient


def main() -> int:
    item_id = os.environ.get("ITEM_ID", "122")
    style_id = os.environ.get("SS_STYLE_ID", "6124")
    color_kw = os.environ.get("MATCH_COLOR", "Black").lower()
    sizes = {s.strip().lower() for s in
             (os.environ.get("MATCH_SIZES", "L,Large").split(",")) if s.strip()}

    ns = NetSuiteClient(get_config().netsuite)
    rows = ns.suiteql(
        "SELECT itemid, vendorname, upccode, custitem_ss_sku, "
        "custitem_mtec_item_sku, custitem_mtec_gtin "
        f"FROM item WHERE id = {int(item_id)}"
    )
    it = rows[0] if rows else {}
    print(f"=== NetSuite item {item_id} ===")
    for k in ("itemid", "vendorname", "upccode", "custitem_ss_sku",
              "custitem_mtec_item_sku", "custitem_mtec_gtin"):
        print(f"  {k:<24} {it.get(k)!r}")
    item_upc = str(it.get("upccode") or "").strip()
    mtec_gtin = str(it.get("custitem_mtec_gtin") or "").strip()

    ss = SsClient(ss_config().ss_api)
    prods = list(ss.iter_products(style_id=style_id))
    print(f"\n=== S&S styleID {style_id}: {len(prods)} products ===")
    style_names = {(p.style_name or "").strip() for p in prods if p.style_name}
    print(f"  S&S style name(s): {sorted(style_names)}")

    hits = [
        p for p in prods
        if color_kw in (p.color_name or "").lower()
        and (p.size_name or "").strip().lower() in sizes
    ]
    print(f"\n=== S&S rows matching color~'{color_kw}' size in {sorted(sizes)} "
          f"({len(hits)}) ===")
    ss_gtins = set()
    for p in hits:
        gtin = str(p.gtin or "").strip()
        ss_gtins.add(gtin)
        print(f"    color={p.color_name!r:<16} size={p.size_name!r:<8} "
              f"sku={p.sku!r} gtin={gtin!r}")
    if not hits:
        colors = sorted({(p.color_name or "") for p in prods})
        print(f"  S&S does NOT carry this color/size. Colors S&S has: {colors}")

    print("\n=== verdict ===")
    print(f"  vendorname == S&S style name? "
          f"{str(it.get('vendorname') or '').strip() in style_names}")
    print(f"  item upcCode in S&S gtins for this color/size? "
          f"{item_upc in ss_gtins if item_upc else 'no upcCode on item'}")
    print(f"  Momentec gtin in S&S gtins? "
          f"{mtec_gtin in ss_gtins if mtec_gtin else 'no mtec gtin'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

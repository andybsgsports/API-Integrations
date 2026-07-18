"""Ground-truth probe for one S&S styleID vs a NetSuite item (read-only).

The user proved S&S DOES carry 695HBM (styleID 6124) -- my earlier probe used
the wrong lookups. This pulls the style the RIGHT way (filtered
``/Products?styleid=``) and checks, against the NetSuite item, exactly why the
sync isn't matching it:

* is the style present in ``iter_styles()`` (so the nightly snapshot pulls it)?
* what gtin / styleName / colorName / sizeName does S&S carry for each SKU?
* does any SKU's gtin equal the item's upccode? does styleName equal the
  item's vendorname? do the color/size names line up with the item's options?

Env: SS_STYLEID (default 6124), SS_ITEM (default 5389).
"""

from __future__ import annotations

import os

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.adopt import COLOR_FIELD, COLOR_LIST, SIZE_FIELD, SIZE_LIST
from sanmar_netsuite.netsuite.client import NetSuiteClient
from ss_activewear_netsuite.config import get_config as ss_config
from ss_activewear_netsuite.ss_activewear.client import SsClient


def main() -> int:
    style_id = os.environ.get("SS_STYLEID", "6124").strip()
    item_id = os.environ.get("SS_ITEM", "5389").strip()
    client = NetSuiteClient(get_config().netsuite)
    ss = SsClient(ss_config().ss_api)

    # -- the NetSuite item we're trying to populate -------------------------
    rows = client.suiteql(
        "SELECT id, itemid, upccode, vendorname, "
        f"{COLOR_FIELD} AS color_id, {SIZE_FIELD} AS size_id "
        f"FROM item WHERE id = {int(item_id)}"
    )
    it = rows[0] if rows else {}
    color_nm = size_nm = ""
    if it.get("color_id"):
        r = client.suiteql(f"SELECT name FROM {COLOR_LIST} WHERE id = {int(it['color_id'])}")
        color_nm = str(r[0]["name"]) if r else ""
    if it.get("size_id"):
        r = client.suiteql(f"SELECT name FROM {SIZE_LIST} WHERE id = {int(it['size_id'])}")
        size_nm = str(r[0]["name"]) if r else ""
    ns_upc = str(it.get("upccode") or "").strip()
    ns_vendor = str(it.get("vendorname") or "").strip()
    print("=== NetSuite item ===")
    print(f"  itemid={it.get('itemid')!r} upccode={ns_upc!r} vendorname={ns_vendor!r}")
    print(f"  color option={color_nm!r}  size option={size_nm!r}")

    # -- is the style in the styles list the nightly snapshot enumerates? ---
    print(f"\n=== iter_styles() membership for styleID {style_id} ===")
    found_style = None
    count = 0
    for st in ss.iter_styles():
        count += 1
        if str(st.style_id) == style_id or (st.style_name or "").upper() == ns_vendor.upper():
            found_style = st
            break
    if found_style:
        print(f"  FOUND after scanning {count} styles: styleID={found_style.style_id} "
              f"name={found_style.style_name!r} brand={found_style.brand_name!r} "
              f"title={found_style.title!r}")
    else:
        print(f"  NOT FOUND in {count} styles -- the nightly snapshot never pulls it")

    # -- what does S&S actually carry for this style? -----------------------
    print(f"\n=== S&S /Products?styleid={style_id} ===")
    prods = list(ss.iter_products(style_id=style_id))
    print(f"  {len(prods)} SKU(s)")
    gtin_hit = style_hit = optmatch = 0
    for p in prods:
        cname = (p.color_name or "").strip()
        sname = (p.size_name or "").strip()
        is_royal_small = "royal" in cname.lower() and sname.upper() in ("S", "SMALL")
        mark = "   <<< Royal/Small" if is_royal_small else ""
        if str(p.gtin).strip() == ns_upc and ns_upc:
            gtin_hit += 1
            mark += "  [GTIN==item.upc]"
        if (p.style_name or "").upper() == ns_vendor.upper():
            style_hit += 1
        if cname.lower() == color_nm.lower() and sname.upper() in (
            size_nm.upper(), size_nm[:1].upper()
        ):
            optmatch += 1
            mark += "  [color+size == item options]"
        if is_royal_small or mark:
            print(f"    sku={p.sku} gtin={p.gtin!r} style={p.style_name!r} "
                  f"color={cname!r} size={sname!r}{mark}")

    print("\n=== why it isn't matching ===")
    print(f"  SKUs whose gtin == item.upccode ({ns_upc!r}): {gtin_hit}")
    print(f"  SKUs whose styleName == item.vendorname ({ns_vendor!r}): {style_hit}")
    print(f"  SKUs whose color+size names == item's options "
          f"({color_nm!r}/{size_nm!r}): {optmatch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Diagnose two S&S issues the user flagged (read-only, runs on CI).

1. An item that lists S&S as a vendor but carries no custitem_ss_* data
   (default: 695HBM-Royal-Small, id 5389). Why didn't the S&S sync match it?
   -> print the item's upc/vendorname, then probe S&S by GTIN and by style.

2. The S&S On-Model Image URL comes through blank. Dump every image-ish
   field S&S returns for a known-matched SKU (default item 84083,
   8000-Black-Small) so we can repoint the on-model field to one that's
   actually populated.

Env: SS_ITEM_UNMATCHED (default 5389), SS_ITEM_MATCHED (default 84083).
"""

from __future__ import annotations

import os

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from ss_activewear_netsuite.config import get_config as ss_config
from ss_activewear_netsuite.ss_activewear.client import SsClient


def item_row(client: NetSuiteClient, item_id: str) -> dict | None:
    rows = client.suiteql(
        "SELECT id, itemid, upccode, vendorname, "
        "custitem_ss_sku, custitem_ss_style, custitem_ss_style_id "
        f"FROM item WHERE id = {int(item_id)}"
    )
    return rows[0] if rows else None


def image_fields(raw: dict) -> dict[str, str]:
    out = {}
    for k, v in raw.items():
        kl = k.lower()
        if "image" in kl or "img" in kl or "swatch" in kl:
            out[k] = str(v)
    return out


def main() -> int:
    unmatched = os.environ.get("SS_ITEM_UNMATCHED", "5389").strip()
    matched = os.environ.get("SS_ITEM_MATCHED", "84083").strip()
    client = NetSuiteClient(get_config().netsuite)
    ss = SsClient(ss_config().ss_api)

    # ---- Q1: why didn't the unmatched item match? -------------------------
    print(f"=== Q1: unmatched item {unmatched} ===")
    it = item_row(client, unmatched)
    if not it:
        print("  item not found")
    else:
        for k in ("itemid", "upccode", "vendorname", "custitem_ss_sku",
                  "custitem_ss_style", "custitem_ss_style_id"):
            print(f"  {k:<24} {it.get(k)!r}")
        upc = str(it.get("upccode") or "").strip()
        vname = str(it.get("vendorname") or "").strip()

        # (a) GTIN probe: does S&S carry this exact barcode?
        print(f"\n  -- S&S GTIN probe for upc {upc!r} --")
        try:
            hit = ss._get(f"/Products/{upc}")
            rows = hit if isinstance(hit, list) else [hit]
            if rows and rows[0]:
                r = rows[0]
                print(f"     S&S HAS this GTIN: sku={r.get('sku')} "
                      f"styleID={r.get('styleID')} style={r.get('styleName')} "
                      f"brand={r.get('brandName')} color={r.get('colorName')} "
                      f"size={r.get('sizeName')} gtin={r.get('gtin')}")
            else:
                print("     no S&S product for that GTIN")
        except Exception as exc:  # noqa: BLE001
            print(f"     GTIN probe returned no match ({str(exc)[:80]})")

        # (b) style probe: is the vendorname a S&S styleName?
        print(f"\n  -- S&S style-name probe for vendorname {vname!r} --")
        try:
            data = ss._get("/Products", params={"style": vname})
            rows = data if isinstance(data, list) else [data]
            rows = [r for r in rows if r]
            print(f"     {len(rows)} S&S product(s) with style={vname!r}")
            for r in rows[:6]:
                print(f"       sku={r.get('sku')} gtin={r.get('gtin')} "
                      f"color={r.get('colorName')} size={r.get('sizeName')} "
                      f"brand={r.get('brandName')}")
        except Exception as exc:  # noqa: BLE001
            print(f"     style probe failed ({str(exc)[:80]})")

        # (c) catalog search: does S&S carry this Russell hoodie at all,
        # under a different (S&S-assigned) style code?
        print("\n  -- S&S /Styles catalog search (Russell / Dri-Power / Hoodie) --")
        try:
            hits = []
            for st in ss.iter_styles():
                brand = (st.brand_name or "").lower()
                title = (st.title or st.style_name or "").lower()
                if "russell" in brand or "dri-power" in title or (
                    "695" in (st.style_name or "")
                ):
                    hits.append(st)
            print(f"     {len(hits)} candidate S&S style(s):")
            for st in hits[:20]:
                print(f"       styleID={st.style_id} name={st.style_name!r} "
                      f"brand={st.brand_name!r} title={st.title!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"     styles search failed ({str(exc)[:100]})")

    # ---- Q2: what image fields does S&S actually return? ------------------
    print(f"\n=== Q2: image fields for matched item {matched} ===")
    it2 = item_row(client, matched)
    if not it2:
        print("  item not found")
        return 0
    sku = str(it2.get("custitem_ss_sku") or "").strip()
    print(f"  itemid={it2.get('itemid')!r} custitem_ss_sku={sku!r} "
          f"upc={it2.get('upccode')!r}")
    probe_key = sku or str(it2.get("upccode") or "").strip()
    if not probe_key:
        print("  no sku/upc to probe")
        return 0
    try:
        raw = ss._get(f"/Products/{probe_key}")
        r = raw[0] if isinstance(raw, list) and raw else raw
        imgs = image_fields(r) if isinstance(r, dict) else {}
        print(f"  S&S returns {len(imgs)} image-ish field(s) for sku {probe_key}:")
        for k, v in sorted(imgs.items()):
            filled = "  <-- POPULATED" if v.strip() else "  (blank)"
            print(f"    {k:<28} {v!r}{filled}")
    except Exception as exc:  # noqa: BLE001
        print(f"  probe failed ({str(exc)[:100]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

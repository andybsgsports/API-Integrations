"""Generalized DC OneSource (PromoStandards) write phase — one script, many
suppliers (runs on CI).

``DCOS_SUPPLIER`` picks the supplier config below. For each: pull the
sellable style list, filter it to the user's price-list allowlist
(``data/pricelist_<key>.csv``, column ``style``) when one exists, find the
styles present in NetSuite by Vendor Name/Code, pull each style's part
detail (``getProduct``) and live availability (``getInventoryLevels``),
match parts to existing items by GTIN -> upcCode then vendorname + matrix
options, and write the ``custitem_<prefix>_*`` key/qty set plus ``upcCode``
where empty. NO pricing is written for these suppliers (user request —
price lists are reference-only for now).

Same machinery as ua_backfill.py, parameterized. Diff-aware; honors
``SYNC_DRY_RUN``; ``UPDATE_MAX_ITEMS`` caps writes.
"""

from __future__ import annotations

import csv
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from sanmar_netsuite.config import get_config as ns_config
from sanmar_netsuite.netsuite.adopt import COLOR_FIELD, SIZE_FIELD, OptionMaps
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape
from sanmar_netsuite.transform.sizes import normalize_size

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_NS = "http://www.promostandards.org/WSDL/ProductDataService/2.0.0/"
INV_NS = "http://www.promostandards.org/WSDL/Inventory/2.0.0/"

# supplier key -> endpoint slug, custitem prefix, display label. Slugs were
# confirmed live via scripts/dcos_brand_probe.py. The allowlist CSV is the
# user's current price list (styles only); absent file = no filtering.
#
# style_source: where the catalog style code lives. "product" = the feed's
# productId IS the style (Champro). "part" = productIds are display names /
# bare numbers and the style is the partId's first dash segment (TCK's
# 'TSK11-026-L', USB's 'SD10100-60512-OSFA-R') -- allowlist filtering and
# NetSuite matching then happen per part.
SUPPLIERS: dict[str, dict[str, str]] = {
    "champro": {"slug": "CHAMPRO", "prefix": "champro", "label": "Champro",
                "style_source": "product"},
    "usb": {"slug": "UNITEDSPORTSBRANDS", "prefix": "usb",
            "label": "United Sports Brands", "style_source": "part"},
    "tck": {"slug": "TWINCITYKNITTING", "prefix": "tck", "label": "Twin City",
            "style_source": "part"},
    "capamerica": {"slug": "CAP", "prefix": "capamerica", "label": "Cap America",
                   "style_source": "product"},
    "mizuno": {"slug": "MIZUNOUSA", "prefix": "mizuno", "label": "Mizuno",
               "style_source": "product"},
    "ripit": {"slug": "RIPIT", "prefix": "ripit", "label": "Rip-It",
              "style_source": "product"},
    "baden": {"slug": "BADENSPORTSINC", "prefix": "baden", "label": "Baden",
              "style_source": "product"},
}


def _soap(url: str, action: str, body: str) -> str:
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        f"<soapenv:Header/><soapenv:Body>{body}</soapenv:Body></soapenv:Envelope>"
    )
    resp = requests.post(
        url, data=envelope.encode(),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": action},
        timeout=180,
    )
    resp.raise_for_status()
    return resp.text


def _strip(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def get_sellable_styles(base: str, key_id: str, key_pw: str) -> list[str]:
    body = (
        f'<ns:GetProductSellableRequest xmlns:ns="{PRODUCT_NS}" '
        f'xmlns:shar="{PRODUCT_NS}SharedObjects/">'
        f"<shar:wsVersion>2.0.0</shar:wsVersion><shar:id>{key_id}</shar:id>"
        f"<shar:password>{key_pw}</shar:password>"
        "<shar:localizationCountry>US</shar:localizationCountry>"
        "<shar:localizationLanguage>en</shar:localizationLanguage>"
        "<ns:isSellable>true</ns:isSellable></ns:GetProductSellableRequest>"
    )
    text = _soap(f"{base}/Product/2.0.0/soap", "getProductSellable", body)
    return sorted(set(re.findall(r"<\s*(?:\w+:)?productId\s*>([^<]+)<", text)))


def get_parts(base: str, key_id: str, key_pw: str, style: str) -> list[dict]:
    body = (
        f'<ns:GetProductRequest xmlns:ns="{PRODUCT_NS}" '
        f'xmlns:shar="{PRODUCT_NS}SharedObjects/">'
        f"<shar:wsVersion>2.0.0</shar:wsVersion><shar:id>{key_id}</shar:id>"
        f"<shar:password>{key_pw}</shar:password>"
        "<shar:localizationCountry>US</shar:localizationCountry>"
        "<shar:localizationLanguage>en</shar:localizationLanguage>"
        f"<shar:productId>{style}</shar:productId></ns:GetProductRequest>"
    )
    text = _soap(f"{base}/Product/2.0.0/soap", "getProduct", body)
    parts: list[dict] = []
    for el in ET.fromstring(text).iter():
        if _strip(el.tag) != "ProductPart":
            continue
        part: dict = {"colors": [], "sizes": []}
        for sub in el.iter():
            t = _strip(sub.tag)
            v = (sub.text or "").strip()
            if t == "partId" and v:
                part["partId"] = v
            elif t == "gtin" and len(v) >= 8 and v.isdigit():
                # Cap America publishes '-' as a placeholder gtin; only keep
                # values that look like real barcodes.
                part["gtin"] = v
            elif t == "colorName" and v:
                part["colors"].append(v)
            elif t == "labelSize" and v:
                part["sizes"].append(v)
        if part.get("partId"):
            parts.append(part)
    return parts


def get_inventory(base: str, key_id: str, key_pw: str, style: str) -> dict[str, int]:
    """partId -> total quantity (DCOS reports a single fulfillment location)."""
    body = (
        f'<ns:GetInventoryLevelsRequest xmlns:ns="{INV_NS}" '
        f'xmlns:shar="{INV_NS}SharedObjects/">'
        f"<shar:wsVersion>2.0.0</shar:wsVersion><shar:id>{key_id}</shar:id>"
        f"<shar:password>{key_pw}</shar:password>"
        f"<shar:productId>{style}</shar:productId></ns:GetInventoryLevelsRequest>"
    )
    try:
        text = _soap(f"{base}/INV/2.0.0/soap", "getInventoryLevels", body)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, int] = {}
    for el in ET.fromstring(text).iter():
        if _strip(el.tag) != "PartInventory":
            continue
        pid, qty = "", 0
        for sub in el.iter():
            t = _strip(sub.tag)
            v = (sub.text or "").strip()
            if t == "partId" and v:
                pid = v
            elif t == "value" and v.replace(".", "").isdigit() and not qty:
                qty = int(float(v))
        if pid:
            out[pid] = qty
    return out


def expand_style_members(raw: str) -> list[str]:
    """Champro publishes some productIds as ranges ('A014-A019') covering
    several catalog styles. Expand to the member styles when the pattern is a
    sane same-prefix numeric span; otherwise the raw id is its own member."""
    m = re.match(r"^([A-Z]*)(\d+)-([A-Z]*)(\d+)$", raw.upper())
    if m and (m.group(3) in ("", m.group(1))):
        pfx, lo, hi = m.group(1), m.group(2), m.group(4)
        if int(hi) >= int(lo) and int(hi) - int(lo) <= 50:
            width = len(lo)
            return [f"{pfx}{n:0{width}d}" for n in range(int(lo), int(hi) + 1)]
    return [raw.upper()]


def load_allowlist(key: str) -> set[str] | None:
    """Uppercased style set from the user's price list, or None if no file."""
    path = ROOT / "data" / f"pricelist_{key}.csv"
    if not path.exists():
        return None
    styles = set()
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            s = (row.get("style") or "").strip().upper()
            if s:
                styles.add(s)
    return styles


def _same(current, new) -> bool:
    cs = ("" if current is None else str(current)).strip()
    ns_ = str(new).strip()
    if cs == ns_:
        return True
    try:
        return float(cs) == float(ns_)
    except ValueError:
        return False


def main() -> int:
    key = (os.environ.get("DCOS_SUPPLIER") or "").strip().lower()
    if key not in SUPPLIERS:
        print(f"DCOS_SUPPLIER must be one of {sorted(SUPPLIERS)} (got {key!r})")
        return 1
    sup = SUPPLIERS[key]
    base = f"https://api.dc-onesource.com/xml/{sup['slug']}"
    prefix = sup["prefix"]
    fields = [
        f"custitem_{prefix}_part_id", f"custitem_{prefix}_style",
        f"custitem_{prefix}_gtin", f"custitem_{prefix}_qty_available",
    ]
    key_id = os.environ["DCOS_KEY_ID"]
    key_pw = os.environ["DCOS_KEY_PASSWORD"]
    allow_write = not ns_config().sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    client = NetSuiteClient(ns_config().netsuite)

    debug = (os.environ.get("DCOS_DEBUG") or "").lower() == "true"
    styles = get_sellable_styles(base, key_id, key_pw)
    print(f"{sup['label']} sellable styles: {len(styles):,}")
    members_of = {s: expand_style_members(s) for s in styles}
    allowlist = load_allowlist(key)
    if allowlist is not None:
        on_list = [s for s in styles
                   if any(mem in allowlist for mem in members_of[s])]
        covered = {mem for s in on_list for mem in members_of[s]} & allowlist
        print(f"price-list allowlist: {len(allowlist):,} styles; "
              f"feed styles on the list: {len(on_list):,} "
              f"(list styles covered incl. ranges: {len(covered):,}; "
              f"missing from feed: {len(allowlist) - len(covered):,})")
        if debug:
            all_members = {mem for s in styles for mem in members_of[s]}
            missing = sorted(allowlist - all_members)
            print(f"  DEBUG feed styles sample: {styles[:25]}")
            print(f"  DEBUG list styles missing from feed (sample): {missing[:25]}")
        styles = on_list
    else:
        print("no price-list allowlist found -- processing the full feed")

    part_mode = sup.get("style_source") == "part"
    present: list[str] = []
    if not part_mode:
        # Which feed styles have NetSuite items? Check every expanded member
        # (vendorname carries the member style, e.g. 'A014', not the range id).
        style_of_member = {mem: s for s in styles for mem in members_of[s]}
        present_members: set[str] = set()
        all_members = sorted(style_of_member)
        for i in range(0, len(all_members), 300):
            chunk = all_members[i : i + 300]
            in_list = ", ".join(f"'{_sql_escape(v)}'" for v in chunk)
            rows = client.suiteql(
                f"SELECT DISTINCT vendorname FROM item WHERE UPPER(vendorname) IN ({in_list})"
            )
            present_members.update(str(r["vendorname"]).upper() for r in rows)
        present = sorted({style_of_member[m] for m in present_members})
        print(f"feed styles with NetSuite items (by vendorname, incl. range "
              f"members): {len(present):,} ({len(present_members):,} member styles)")

    hint = (os.environ.get("DCOS_DEBUG_HINT") or "").strip().lower()
    if debug and hint:
        # Sample items whose display name mentions the brand, to learn how
        # this supplier's items are keyed when style lookups find nothing.
        rows = client.suiteql(
            "SELECT itemid, vendorname, upccode FROM item "
            f"WHERE LOWER(displayname) LIKE '%{_sql_escape(hint)}%' AND rownum <= 12"
        )
        print(f"  DEBUG name-hint '{hint}': {len(rows)} sample items")
        for r in rows:
            print(f"    itemid={r.get('itemid')!r} vendorname={r.get('vendorname')!r} "
                  f"upccode={r.get('upccode')!r}")
    if debug:
        # How are this supplier's items ACTUALLY keyed in NetSuite? Probe by
        # itemid prefix (the STYLE-COLOR-SIZE naming convention) for a sample
        # of allowlist styles, and dump feed part detail for a few styles.
        probe_styles = (sorted(allowlist)[:400] if allowlist else styles[:400])
        hits = 0
        for s in probe_styles[:60]:
            safe = _sql_escape(s)
            rows = client.suiteql(
                f"SELECT id, itemid, vendorname, upccode FROM item "
                f"WHERE itemid LIKE '{safe}-%' AND rownum <= 3"
            )
            if rows:
                hits += 1
                if hits <= 8:
                    for r in rows:
                        print(f"  DEBUG itemid-prefix hit [{s}]: itemid={r.get('itemid')!r} "
                              f"vendorname={r.get('vendorname')!r} upccode={r.get('upccode')!r}")
        print(f"  DEBUG itemid-prefix: {hits}/60 sampled styles have 'STYLE-%' items")
        # When the allowlist filter empties the pool (feed productIds aren't
        # style codes, e.g. TCK's are product NAMES), sample raw feed products
        # so the part detail still reveals where the style codes live.
        sample = (present[:2] + [x for x in styles if x not in present][:1]) \
            or list(members_of)[:3]
        for s in sample:
            try:
                parts = get_parts(base, key_id, key_pw, s)
            except Exception as exc:  # noqa: BLE001
                print(f"  DEBUG getProduct({s}) failed: {str(exc)[:80]}")
                continue
            with_gtin = sum(1 for p in parts if p.get("gtin"))
            print(f"  DEBUG parts[{s}]: {len(parts)} parts, {with_gtin} with gtin; "
                  f"sample: {[{k: p.get(k) for k in ('partId','gtin','colors','sizes')} for p in parts[:3]]}")

    # -- collect (style, parts, inv) work units per style_source mode --------
    units: list[tuple[str, list[dict], dict[str, int]]] = []
    if part_mode:
        # productIds aren't styles (TCK: product names; USB: bare numbers) --
        # harvest every feed product's parts and regroup by the partId's
        # first dash segment, allowlist-filtered.
        by_style: dict[str, list[dict]] = {}
        inv_all: dict[str, int] = {}
        feed_products = sorted(members_of)
        for n, product in enumerate(feed_products, 1):
            try:
                parts = get_parts(base, key_id, key_pw, product)
            except Exception as exc:  # noqa: BLE001
                print(f"  getProduct failed for {product}: {str(exc)[:100]}")
                continue
            inv_all.update(get_inventory(base, key_id, key_pw, product))
            for p in parts:
                st = p["partId"].split("-")[0].strip().upper()
                if not st:
                    continue
                # TCK appends a numeric suffix to base styles ('TSK' on the
                # price list -> 'TSK11' in partIds and NetSuite itemids), so
                # accept the alpha base too.
                base_st = re.sub(r"\d+$", "", st)
                if allowlist is not None and st not in allowlist \
                        and base_st not in allowlist:
                    continue
                by_style.setdefault(st, []).append(p)
            if n % 25 == 0:
                print(f"  ...{n}/{len(feed_products)} feed products harvested; "
                      f"{len(by_style)} allowlisted styles so far")
        print(f"part-derived styles on the price list: {len(by_style):,}")
        if debug:
            print(f"  DEBUG part-derived styles: {sorted(by_style)}")
        units = [(st, ps, inv_all) for st, ps in sorted(by_style.items())]
    else:
        for style in sorted(present):
            try:
                parts = get_parts(base, key_id, key_pw, style)
            except Exception as exc:  # noqa: BLE001
                print(f"  getProduct failed for {style}: {str(exc)[:100]}")
                continue
            units.append((style, parts, get_inventory(base, key_id, key_pw, style)))

    options = OptionMaps(client)
    matched: dict[str, dict] = {}
    for n, (style, parts, inv) in enumerate(units, 1):
        in_list = ", ".join(
            f"'{_sql_escape(m)}'" for m in members_of.get(style, [style])
        )
        rows = client.suiteql(
            f"SELECT id, itemid, upccode, {COLOR_FIELD} AS color, {SIZE_FIELD} AS size "
            f"FROM item WHERE UPPER(vendorname) IN ({in_list})"
        )
        by_upc = {str(r.get("upccode") or ""): str(r["id"]) for r in rows if r.get("upccode")}
        opt_index = {
            (str(r.get("color") or ""), str(r.get("size") or "")): str(r["id"])
            for r in rows if r.get("color") and r.get("size")
        }
        # itemid 'STYLE-COLOR[-SIZE]' -> (color, size) segments, for feeds
        # without GTINs: match part color names against the name segment.
        seg_info: dict[str, tuple[str, str]] = {}
        for r in rows:
            toks = str(r.get("itemid") or "").split("-", 2)
            if len(toks) >= 2:
                seg_info[str(r["id"])] = (
                    toks[1].strip().lower(),
                    toks[2].strip() if len(toks) >= 3 else "",
                )
        if debug and n <= 12:
            sample = (f"; item sample: {rows[0].get('itemid')!r} "
                      f"vendorname={rows[0].get('vendorname')!r}") if rows else ""
            print(f"  DEBUG unit[{style}]: {len(parts)} parts "
                  f"(partIds: {[p['partId'] for p in parts[:3]]}); "
                  f"{len(rows)} NetSuite items{sample}")

        def claim(rid: str, part: dict) -> None:
            if rid and rid not in matched:
                matched[rid] = {
                    f"custitem_{prefix}_part_id": part["partId"],
                    f"custitem_{prefix}_style": style,
                    f"custitem_{prefix}_gtin": part.get("gtin", ""),
                    f"custitem_{prefix}_qty_available": inv.get(part["partId"]),
                }

        for part in parts:
            rid = by_upc.get(part.get("gtin", ""))
            if not rid and options.available and opt_index:
                color = (part["colors"][0] if part["colors"] else "").strip()
                size = (part["sizes"][0] if part["sizes"] else "").strip()
                size_ids = list(dict.fromkeys(
                    options.size_candidates(size)
                    + options.size_candidates(normalize_size(size))
                ))
                for cid, _m in options.color_candidates(color, color):
                    rid = next((opt_index[(cid, sid)] for sid in size_ids
                                if (cid, sid) in opt_index), None)
                    if rid:
                        break
            if rid:
                claim(rid, part)
                continue
            # color-segment pass: every item whose itemid color matches.
            # In part mode the partId's last token is the size (TCK's
            # 'TSK11-026-L') -- require it to agree with the itemid's size
            # segment so same-color sizes don't collapse onto one part.
            # Mizuno decorates color names with codes and hand prefixes
            # ('BLACK (9090)', 'LEFT HAND: BLACK-TAN (F981)') -- strip them.
            part_colors = set()
            for c in part["colors"]:
                c = re.sub(r"\(.*?\)", "", c)
                c = re.sub(r"^(?:LEFT|RIGHT)\s+HAND:\s*", "", c, flags=re.I)
                c = c.strip().lower()
                if c:
                    part_colors.add(c)
            psize = ""
            if part_mode:
                toks = part["partId"].split("-")
                if len(toks) >= 3:
                    psize = normalize_size(toks[-1]).strip().lower()
            if part_colors:
                for iid, (cseg, sseg) in seg_info.items():
                    if cseg not in part_colors:
                        continue
                    if (psize and sseg
                            and normalize_size(sseg).strip().lower() != psize):
                        continue
                    claim(iid, part)
        if len(parts) == 1:
            # single-part style (one price/qty for the whole style): the part
            # applies to every item of the style not claimed above.
            for r in rows:
                claim(str(r["id"]), parts[0])
        if n % 50 == 0:
            print(f"  ...{n}/{len(units)} styles processed; matched so far {len(matched):,}")
    print(f"matched items: {len(matched):,}")

    ids = sorted(matched)
    cols = ", ".join(fields)
    considered = written = unchanged = upc_filled = failures = 0
    for i in range(0, len(ids), 250):
        chunk = ids[i : i + 250]
        in_list = ", ".join(f"'{_sql_escape(x)}'" for x in chunk)
        for row in client.suiteql(
            f"SELECT id, upccode, {cols} FROM item WHERE id IN ({in_list})"
        ):
            rid = str(row["id"])
            want = {k: v for k, v in matched.get(rid, {}).items()
                    if v is not None and str(v).strip() != ""}
            body = {f: v for f, v in want.items() if not _same(row.get(f), v)}
            gtin = matched.get(rid, {}).get(f"custitem_{prefix}_gtin", "")
            if not str(row.get("upccode") or "").strip() and gtin:
                body["upcCode"] = gtin
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
                client.update_record("inventoryItem", rid, body)
                written += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if failures <= 10:
                    print(f"  FAILED item {rid}: {str(exc)[:150]}")

    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(f"\n{key} backfill: {verb} {written} item(s); unchanged: {unchanged}; "
          f"upcCode filled (was empty): {upc_filled}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

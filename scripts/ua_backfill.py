"""Under Armour write phase (DC OneSource / PromoStandards), runs on CI.

Finds the UA styles present in the catalog by Vendor Name/Code, pulls each
style's part detail (``getProduct``), live availability
(``getInventoryLevels``), and pricing (``getConfigurationAndPricing``),
matches parts to existing items by GTIN -> upcCode then vendorname + matrix
options, and writes the ``custitem_ua_*`` set plus ``upcCode`` where empty.

Pricing goes to the NATIVE fields, not custom ones: the feed's single
published price (DC OneSource returns the same number for Net/List/Customer;
values sit at UA retail price points, i.e. list/MSRP) is written to Base
Price, and Purchase Price (``cost``) is derived from it via ``UA_COST_PCT``
(cost = price * pct / 100). Without ``UA_COST_PCT``, only Base Price is
written. Diff-aware; honors ``SYNC_DRY_RUN``; ``UPDATE_MAX_ITEMS`` caps
writes.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

import requests

from sanmar_netsuite.config import get_config as ns_config
from sanmar_netsuite.netsuite.adopt import COLOR_FIELD, SIZE_FIELD, OptionMaps
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape
from sanmar_netsuite.transform.sizes import normalize_size

BASE = "https://api.dc-onesource.com/xml/UNDERARMOR"
PRODUCT_NS = "http://www.promostandards.org/WSDL/ProductDataService/2.0.0/"
INV_NS = "http://www.promostandards.org/WSDL/Inventory/2.0.0/"
PPC_NS = "http://www.promostandards.org/WSDL/PricingAndConfiguration/1.0.0/"
BASE_PRICE_LEVEL = "1"  # same identifier set reconcile.py uses

FIELDS = [
    "custitem_ua_part_id", "custitem_ua_style", "custitem_ua_gtin",
    "custitem_ua_qty_available", "custitem_ua_qty_by_whse",
]


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


def _walk(text: str):
    root = ET.fromstring(text)
    for el in root.iter():
        yield _strip(el.tag), el


def get_sellable_styles(key_id: str, key_pw: str) -> list[str]:
    body = (
        f'<ns:GetProductSellableRequest xmlns:ns="{PRODUCT_NS}" '
        f'xmlns:shar="{PRODUCT_NS}SharedObjects/">'
        f"<shar:wsVersion>2.0.0</shar:wsVersion><shar:id>{key_id}</shar:id>"
        f"<shar:password>{key_pw}</shar:password>"
        "<shar:localizationCountry>US</shar:localizationCountry>"
        "<shar:localizationLanguage>en</shar:localizationLanguage>"
        "<ns:isSellable>true</ns:isSellable></ns:GetProductSellableRequest>"
    )
    text = _soap(f"{BASE}/Product/2.0.0/soap", "getProductSellable", body)
    return sorted(set(re.findall(r"<\s*(?:\w+:)?productId\s*>([^<]+)<", text)))


def get_parts(key_id: str, key_pw: str, style: str) -> list[dict]:
    body = (
        f'<ns:GetProductRequest xmlns:ns="{PRODUCT_NS}" '
        f'xmlns:shar="{PRODUCT_NS}SharedObjects/">'
        f"<shar:wsVersion>2.0.0</shar:wsVersion><shar:id>{key_id}</shar:id>"
        f"<shar:password>{key_pw}</shar:password>"
        "<shar:localizationCountry>US</shar:localizationCountry>"
        "<shar:localizationLanguage>en</shar:localizationLanguage>"
        f"<shar:productId>{style}</shar:productId></ns:GetProductRequest>"
    )
    text = _soap(f"{BASE}/Product/2.0.0/soap", "getProduct", body)
    parts: list[dict] = []
    root = ET.fromstring(text)
    for el in root.iter():
        if _strip(el.tag) != "ProductPart":
            continue
        part: dict = {"colors": [], "sizes": []}
        for sub in el.iter():
            t = _strip(sub.tag)
            v = (sub.text or "").strip()
            if t == "partId" and v:
                part["partId"] = v
            elif t == "gtin" and v:
                part["gtin"] = v
            elif t == "colorName" and v:
                part["colors"].append(v)
            elif t == "labelSize" and v:
                part["sizes"].append(v)
        if part.get("partId"):
            parts.append(part)
    return parts


def get_style_pricing(key_id: str, key_pw: str, style: str) -> dict[str, float]:
    """partId -> published price for one style, or {} if pricing unavailable."""
    fob_body = (
        f'<ns:GetFobPointsRequest xmlns:ns="{PPC_NS}" '
        f'xmlns:shar="{PPC_NS}SharedObjects/">'
        f"<shar:wsVersion>1.0.0</shar:wsVersion><shar:id>{key_id}</shar:id>"
        f"<shar:password>{key_pw}</shar:password>"
        f"<shar:productId>{style}</shar:productId>"
        "<shar:localizationCountry>US</shar:localizationCountry>"
        "<shar:localizationLanguage>en</shar:localizationLanguage>"
        "</ns:GetFobPointsRequest>"
    )
    try:
        text = _soap(f"{BASE}/PPC/1.0.0/soap", "getFobPoints", fob_body)
    except Exception:  # noqa: BLE001
        return {}
    fob = re.search(r"<\s*(?:\w+:)?fobId\s*>([^<]+)<", text)
    if not fob:
        return {}
    price_body = (
        f'<ns:GetConfigurationAndPricingRequest xmlns:ns="{PPC_NS}" '
        f'xmlns:shar="{PPC_NS}SharedObjects/">'
        f"<shar:wsVersion>1.0.0</shar:wsVersion><shar:id>{key_id}</shar:id>"
        f"<shar:password>{key_pw}</shar:password>"
        f"<shar:productId>{style}</shar:productId>"
        "<shar:currency>USD</shar:currency>"
        f"<shar:fobId>{fob.group(1)}</shar:fobId>"
        "<shar:priceType>List</shar:priceType>"
        "<shar:localizationCountry>US</shar:localizationCountry>"
        "<shar:localizationLanguage>en</shar:localizationLanguage>"
        "<shar:configurationType>Blank</shar:configurationType>"
        "</ns:GetConfigurationAndPricingRequest>"
    )
    try:
        text = _soap(f"{BASE}/PPC/1.0.0/soap", "getConfigurationAndPricing", price_body)
    except Exception:  # noqa: BLE001
        return {}
    parts = re.findall(r"<\s*(?:\w+:)?partId\s*>([^<]+)<", text)
    prices = re.findall(r"<\s*(?:\w+:)?price\s*>([^<]+)<", text)
    out: dict[str, float] = {}
    for p, pr in zip(parts, prices):
        try:
            out[p] = float(pr)
        except ValueError:
            continue
    return out


def get_inventory(key_id: str, key_pw: str, style: str) -> dict[str, tuple[int, str]]:
    body = (
        f'<ns:GetInventoryLevelsRequest xmlns:ns="{INV_NS}" '
        f'xmlns:shar="{INV_NS}SharedObjects/">'
        f"<shar:wsVersion>2.0.0</shar:wsVersion><shar:id>{key_id}</shar:id>"
        f"<shar:password>{key_pw}</shar:password>"
        f"<shar:productId>{style}</shar:productId></ns:GetInventoryLevelsRequest>"
    )
    try:
        text = _soap(f"{BASE}/INV/2.0.0/soap", "getInventoryLevels", body)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, tuple[int, str]] = {}
    root = ET.fromstring(text)
    for el in root.iter():
        if _strip(el.tag) != "PartInventory":
            continue
        pid, qty, whse = "", 0, []
        for sub in el.iter():
            t = _strip(sub.tag)
            v = (sub.text or "").strip()
            if t == "partId" and v:
                pid = v
            elif t == "quantityAvailable":
                pass
            elif t == "value" and v.replace(".", "").isdigit() and not qty:
                qty = int(float(v))
            elif t == "inventoryLocationId" and v:
                whse.append(v)
        if pid:
            out[pid] = (qty, "; ".join(whse))
    return out


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
    key_id = os.environ["DCOS_KEY_ID"]
    key_pw = os.environ["DCOS_KEY_PASSWORD"]
    allow_write = not ns_config().sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    cost_pct = float(os.environ.get("UA_COST_PCT", "0") or "0")
    if cost_pct:
        print(f"cost = list price x {cost_pct}%")
    else:
        print("UA_COST_PCT not set -- writing Base Price only, cost untouched")
    client = NetSuiteClient(ns_config().netsuite)

    styles = get_sellable_styles(key_id, key_pw)
    print(f"UA sellable styles: {len(styles):,}")
    present: list[str] = []
    for i in range(0, len(styles), 300):
        chunk = styles[i : i + 300]
        in_list = ", ".join(f"'{_sql_escape(v)}'" for v in chunk)
        rows = client.suiteql(
            f"SELECT DISTINCT vendorname FROM item WHERE vendorname IN ({in_list})"
        )
        present.extend(str(r["vendorname"]) for r in rows)
    print(f"styles present by vendorname: {len(present):,}")

    options = OptionMaps(client)
    matched: dict[str, dict] = {}
    price_by_rid: dict[str, float] = {}
    for n, style in enumerate(sorted(present), 1):
        try:
            parts = get_parts(key_id, key_pw, style)
        except Exception as exc:  # noqa: BLE001
            print(f"  getProduct failed for {style}: {str(exc)[:100]}")
            continue
        inv = get_inventory(key_id, key_pw, style)
        prices = get_style_pricing(key_id, key_pw, style)
        safe = _sql_escape(style)
        rows = client.suiteql(
            f"SELECT id, upccode, {COLOR_FIELD} AS color, {SIZE_FIELD} AS size "
            f"FROM item WHERE vendorname = '{safe}'"
        )
        by_upc = {str(r.get("upccode") or ""): str(r["id"]) for r in rows if r.get("upccode")}
        opt_index = {
            (str(r.get("color") or ""), str(r.get("size") or "")): str(r["id"])
            for r in rows if r.get("color") and r.get("size")
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
            if rid and rid not in matched:
                qty, whse = inv.get(part["partId"], (None, ""))
                matched[rid] = {
                    "custitem_ua_part_id": part["partId"],
                    "custitem_ua_style": style,
                    "custitem_ua_gtin": part.get("gtin", ""),
                    "custitem_ua_qty_available": qty,
                    "custitem_ua_qty_by_whse": whse,
                }
                list_price = prices.get(part["partId"])
                if list_price is not None:
                    price_by_rid[rid] = list_price
        if n % 50 == 0:
            print(f"  ...{n}/{len(present)} styles processed; matched so far {len(matched):,}")
    print(f"matched items: {len(matched):,}")

    print(f"items with a feed price: {len(price_by_rid):,}")
    ids = sorted(matched)
    cols = ", ".join(FIELDS)
    considered = written = unchanged = upc_filled = priced = failures = 0
    for i in range(0, len(ids), 250):
        chunk = ids[i : i + 250]
        in_list = ", ".join(f"'{_sql_escape(x)}'" for x in chunk)
        # Current Base Price per item, for the diff. The pricing table carries
        # one row per item/level; tolerate the query failing (write anyway).
        base_by_rid: dict[str, str] = {}
        try:
            for r in client.suiteql(
                f"SELECT item, unitprice FROM pricing "
                f"WHERE pricelevel = {BASE_PRICE_LEVEL} AND item IN ({in_list})"
            ):
                base_by_rid[str(r["item"])] = str(r.get("unitprice") or "")
        except Exception as exc:  # noqa: BLE001
            print(f"  (base-price read failed, writing unconditionally: {str(exc)[:80]})")
        for row in client.suiteql(
            f"SELECT id, upccode, cost, {cols} FROM item WHERE id IN ({in_list})"
        ):
            rid = str(row["id"])
            want = {k: v for k, v in matched.get(rid, {}).items()
                    if v is not None and str(v).strip() != ""}
            body = {f: v for f, v in want.items() if not _same(row.get(f), v)}
            gtin = matched.get(rid, {}).get("custitem_ua_gtin", "")
            if not str(row.get("upccode") or "").strip() and gtin:
                body["upcCode"] = gtin
            list_price = price_by_rid.get(rid)
            if list_price is not None:
                if not _same(base_by_rid.get(rid), list_price):
                    body["price"] = {
                        "items": [
                            {
                                "currencyPage": 1,
                                "priceLevel": {"id": BASE_PRICE_LEVEL},
                                "quantity": {"value": 0},
                                "price": list_price,
                            }
                        ]
                    }
                if cost_pct:
                    cost = round(list_price * cost_pct / 100.0, 2)
                    if not _same(row.get("cost"), cost):
                        body["cost"] = cost
            if not body:
                unchanged += 1
                continue
            if max_items and considered >= max_items:
                continue
            considered += 1
            if "upcCode" in body:
                upc_filled += 1
            if "price" in body or "cost" in body:
                priced += 1
            if not allow_write:
                written += 1
                continue
            try:
                client.update_record("inventoryItem", rid, body)
                written += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if failures <= 10:
                    detail = getattr(exc, "payload", "")
                    print(f"  FAILED item {rid}: {str(exc)[:100]} :: {str(detail)[:300]}")

    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(f"\nua backfill: {verb} {written} item(s); unchanged: {unchanged}; "
          f"upcCode filled (was empty): {upc_filled}; "
          f"price/cost updated: {priced}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

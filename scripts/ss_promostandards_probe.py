"""Probe S&S's PromoStandards Inventory 2.0.0 service and compare to REST.

S&S's API Developer guides describe two ways to pull per-warehouse inventory:
  * the REST endpoint we use today  (/Inventory/{sku})
  * the PromoStandards GetInventoryLevels 2.0.0 SOAP service, which S&S's guide
    explicitly *recommends* for inventory-by-warehouse and which returns each
    location's full name + address (not just the 2-letter code).

This read-only probe:
  1. discovers the working Inventory 2.0.0 endpoint (tries the documented host
     + a few candidate paths / the WSDL soap:address),
  2. calls GetInventoryLevels for a style (default B06560 = Gildan 8000, the
     "flat 500" style, and B00760 = Gildan 2000 as a control),
  3. prints per-warehouse qty + location name/address from PromoStandards, and
  4. for one SKU, prints the REST /Inventory numbers side by side.

Nothing is written. Env: SS_PS_PRODUCT_IDS (comma list, default
"B06560,B00760"), SS_PS_COMPARE_SKU (default B06560504).
"""

from __future__ import annotations

import os
import re
from xml.etree import ElementTree as ET

import requests

from ss_activewear_netsuite.config import get_config as ss_config
from ss_activewear_netsuite.ss_activewear.client import SsClient

NS_BODY = "http://www.promostandards.org/WSDL/Inventory/2.0.0/"
NS_SHAR = "http://www.promostandards.org/WSDL/Inventory/2.0.0/SharedObjects/"

# Documented host is promostandards.ssactivewear.com; the exact service path
# isn't in the PDF, so try the common PromoStandards layouts and the WSDL.
ENDPOINT_CANDIDATES = [
    "https://promostandards.ssactivewear.com/Inventory/v2/InventoryService.svc",
    "https://promostandards.ssactivewear.com/Inventory/v2/InventoryService.svc/soap",
    "https://promostandards.ssactivewear.com/inventoryservice/2.0.0/inventoryservice.svc",
    "https://promostandards.ssactivewear.com/Inventory/2.0.0/InventoryService.svc",
]


def soap_envelope(account: str, key: str, product_id: str) -> str:
    return (
        '<soapenv:Envelope '
        'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:ns="{NS_BODY}" xmlns:shar="{NS_SHAR}">'
        "<soapenv:Header/><soapenv:Body>"
        "<ns:GetInventoryLevelsRequest>"
        "<shar:wsVersion>2.0.0</shar:wsVersion>"
        f"<shar:id>{account}</shar:id>"
        f"<shar:password>{key}</shar:password>"
        f"<shar:productId>{product_id}</shar:productId>"
        "</ns:GetInventoryLevelsRequest>"
        "</soapenv:Body></soapenv:Envelope>"
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el, name):
    for d in el.iter():
        if _local(d.tag) == name:
            return d
    return None


def _findall(el, name):
    return [d for d in el.iter() if _local(d.tag) == name]


def discover_endpoint(client: requests.Session) -> list[str]:
    """Return candidate endpoints, front-loading any the WSDL advertises."""
    working = []
    for base in ENDPOINT_CANDIDATES:
        try:
            r = client.get(base + "?wsdl", timeout=25, allow_redirects=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  WSDL {base}?wsdl -> {type(exc).__name__}: {str(exc)[:80]}")
            continue
        print(f"  WSDL {base}?wsdl -> HTTP {r.status_code} ({len(r.text)} bytes)")
        if r.status_code == 200 and "definitions" in r.text[:2000].lower():
            for m in re.finditer(r'location="([^"]+)"', r.text):
                loc = m.group(1)
                if loc.startswith("http") and loc not in working:
                    working.append(loc)
                    print(f"    soap:address -> {loc}")
    return working + [c for c in ENDPOINT_CANDIDATES if c not in working]


def call_inventory(client: requests.Session, url: str, body: str) -> requests.Response | None:
    for action in ("getInventoryLevels", ""):
        headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": action}
        try:
            r = client.post(url, data=body.encode(), headers=headers, timeout=40,
                            allow_redirects=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  POST {url} (action={action!r}) -> {type(exc).__name__}: {str(exc)[:80]}")
            continue
        print(f"  POST {url} (action={action!r}) -> HTTP {r.status_code} ({len(r.text)} bytes)")
        if r.status_code == 200 and "GetInventoryLevelsResponse" in r.text:
            return r
        if r.status_code == 500 and "faultstring" in r.text:
            m = re.search(r"<faultstring>(.*?)</faultstring>", r.text, re.S)
            print(f"    SOAP fault: {(m.group(1).strip()[:160]) if m else '?'}")
    return None


# code -> (name, city, region/state); filled across every part we parse so we
# end with the authoritative, deduped warehouse identity list.
WHSE_MAP: dict[str, tuple[str, str, str]] = {}


def _txt(el, name) -> str:
    d = _find(el, name)
    return (d.text or "").strip() if d is not None and d.text else ""


def parse_and_print(xml_text: str) -> None:
    root = ET.fromstring(xml_text.encode())
    parts = _findall(root, "PartInventory")
    print(f"  PromoStandards returned {len(parts)} part(s)")
    for p in parts[:6]:
        pid = _txt(p, "partId")
        head = f"    part {pid}  {_txt(p, 'partColor')}/{_txt(p, 'labelSize')}"
        qa = _find(p, "quantityAvailable")
        head += f"  total={_txt(qa, 'value') if qa is not None else '?'}"
        print(head)
        for loc in _findall(p, "InventoryLocation"):
            code = _txt(loc, "inventoryLocationId")
            name = _txt(loc, "inventoryLocationName")
            addr = _find(loc, "Address")
            city = _txt(addr, "city") if addr is not None else ""
            region = _txt(addr, "region") if addr is not None else ""
            q = _find(loc, "inventoryLocationQuantity")
            qty = _txt(q, "value") if q is not None else "?"
            if code:
                WHSE_MAP[code] = (name, city, region)
            print(f"        {code:<4} {name:<14} {city:<14} {region:<4} qty={qty}")


def main() -> int:
    cfg = ss_config().ss_api
    account = getattr(cfg, "account_number", None) or os.environ.get("SS_API_ACCOUNT_NUMBER", "")
    key = getattr(cfg, "api_key", None) or os.environ.get("SS_API_KEY", "")
    if not account or not key:
        print("missing SS account/key")
        return 1
    product_ids = [p.strip() for p in
                   (os.environ.get("SS_PS_PRODUCT_IDS") or "B06560,B00760").split(",")
                   if p.strip()]
    compare_sku = os.environ.get("SS_PS_COMPARE_SKU") or "B06560504"

    client = requests.Session()
    print("=== discovering PromoStandards Inventory 2.0.0 endpoint ===")
    endpoints = discover_endpoint(client)

    working_endpoint = ""
    for pid in product_ids:
        print(f"\n=== GetInventoryLevels 2.0.0 for productId {pid} ===")
        body = soap_envelope(account, key, pid)
        got = None
        for url in endpoints:
            got = call_inventory(client, url, body)
            if got is not None:
                working_endpoint = url
                print(f"  >>> endpoint that worked: {url}")
                break
        if got is None:
            print("  no endpoint returned a valid GetInventoryLevelsResponse")
            continue
        try:
            parse_and_print(got.text)
        except Exception as exc:  # noqa: BLE001
            print(f"  parse error: {type(exc).__name__}: {str(exc)[:120]}")
            print(got.text[:800])

    print("\n=== AUTHORITATIVE WAREHOUSE MAP (deduped across parts) ===")
    print(f"  working endpoint: {working_endpoint or '(none found)'}")
    for code in sorted(WHSE_MAP):
        name, city, region = WHSE_MAP[code]
        print(f"  {code:<4} name={name!r:<16} city={city!r:<16} region={region!r}")

    print(f"\n=== REST /Inventory/{compare_sku} (what we use today) ===")
    ss = SsClient(cfg)
    try:
        inv = ss.get_inventory(compare_sku)
        if inv is None:
            print("  REST returned no record")
        else:
            for w in inv.warehouses:
                print(f"    {w.warehouse_abbr:<4} qty={int(w.qty or 0)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  REST call failed: {str(exc)[:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

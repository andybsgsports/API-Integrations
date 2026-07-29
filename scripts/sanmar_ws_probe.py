"""Read-only probe of SanMar's Web Services (ws.sanmar.com, PromoStandards).

The FTP files this integration consumes have two known blind spots, both
confirmed against the live feed:

* no closeout signal -- PRODUCTSTATUS only ever carries Regular / Active /
  Discontinued, and the CLOSEOUT title-prefix idea matched 0 of 160,404 SKUs;
* inventory capped at 1500/warehouse -- sanmar_dip.txt reports at most 1500,
  and 13k+ SKUs sit exactly at the cap.

SanMar's PromoStandards web services carry both: Product Data 2.0.0 has an
``isCloseout`` boolean per product, and Inventory 2.0.0 returns true on-hand
quantities. This probe calls each once and prints what comes back, so wiring a
real sync starts from evidence instead of another 1h45m guess.

Auth: PromoStandards wants ``id`` (SanMar customer number) + ``password``.
SANMAR_WS_ID / SANMAR_WS_PASSWORD override; otherwise the SFTP credentials are
tried, since SanMar commonly provisions them together. Whatever happens is
reported (which combination was tried, never the secrets themselves).

Writes nothing, touches NetSuite not at all.
"""

from __future__ import annotations

import os
import re
from xml.sax.saxutils import escape

import requests

WS_BASE = os.environ.get("SANMAR_WS_BASE", "https://ws.sanmar.com:8080")
PRODUCT_ENDPOINT = f"{WS_BASE}/promostandards/ProductDataServiceV2.svc"
INVENTORY_ENDPOINT = f"{WS_BASE}/promostandards/InventoryServiceBindingV2final.svc"

# Styles to ask about. S500T showed as Discontinued with 0 stock in the last
# sample -- a plausible closeout candidate; 29M is a live evergreen control.
PROBE_STYLES = [s for s in os.environ.get(
    "SANMAR_WS_STYLES", "29M,S500T,PC61"
).split(",") if s.strip()]

PRODUCT_NS = "http://www.promostandards.org/WSDL/ProductDataService/2.0.0/"
PRODUCT_SHARED = f"{PRODUCT_NS}SharedObjects/"
INVENTORY_NS = "http://www.promostandards.org/WSDL/Inventory/2.0.0/"
INVENTORY_SHARED = f"{INVENTORY_NS}SharedObjects/"


def _creds() -> tuple[str, str, str]:
    """(id, password, which) -- explicit WS creds, else the SFTP pair."""
    ws_id = os.environ.get("SANMAR_WS_ID", "").strip()
    ws_pw = os.environ.get("SANMAR_WS_PASSWORD", "").strip()
    if ws_id and ws_pw:
        return ws_id, ws_pw, "SANMAR_WS_ID/SANMAR_WS_PASSWORD"
    return (
        os.environ.get("SANMAR_SFTP_USERNAME", "").strip(),
        os.environ.get("SANMAR_SFTP_PASSWORD", "").strip(),
        "SFTP credentials (SanMar often provisions FTP+WS together)",
    )


def _call(endpoint: str, action: str, body: str) -> tuple[int, str]:
    resp = requests.post(
        endpoint,
        data=body.encode(),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": action},
        timeout=60,
    )
    return resp.status_code, resp.text


def _tag(xml: str, name: str) -> list[str]:
    """Text of every <ns:name>...</ns:name> occurrence, namespace-agnostic."""
    return re.findall(
        rf"<(?:\w+:)?{name}(?:\s[^>]*)?>([^<]*)</(?:\w+:)?{name}>", xml
    )


def probe_product(pid: str, pw: str, style: str) -> None:
    body = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:ns="{PRODUCT_NS}" xmlns:shar="{PRODUCT_SHARED}">
 <soapenv:Header/>
 <soapenv:Body>
  <ns:GetProductRequest>
   <shar:wsVersion>2.0.0</shar:wsVersion>
   <shar:id>{escape(pid)}</shar:id>
   <shar:password>{escape(pw)}</shar:password>
   <shar:localizationCountry>US</shar:localizationCountry>
   <shar:localizationLanguage>en</shar:localizationLanguage>
   <shar:productId>{escape(style)}</shar:productId>
  </ns:GetProductRequest>
 </soapenv:Body>
</soapenv:Envelope>"""
    try:
        status, text = _call(PRODUCT_ENDPOINT, "getProduct", body)
    except requests.RequestException as exc:
        print(f"  {style}: REQUEST FAILED: {str(exc)[:140]}")
        return
    codes = _tag(text, "code")
    descs = _tag(text, "description")
    if codes or "Fault" in text[:2000]:
        print(f"  {style}: HTTP {status}; service message: "
              f"{codes[:1]} {descs[:1] or text[:160]!r}")
        return
    name = _tag(text, "productName")
    closeout = _tag(text, "isCloseout")
    brand = _tag(text, "productBrand")
    print(f"  {style}: HTTP {status}  productName={name[:1]}  "
          f"brand={brand[:1]}  isCloseout={closeout[:1] or 'NOT PRESENT'}")


def probe_inventory(pid: str, pw: str, style: str) -> None:
    body = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:ns="{INVENTORY_NS}" xmlns:shar="{INVENTORY_SHARED}">
 <soapenv:Header/>
 <soapenv:Body>
  <ns:GetInventoryLevelsRequest>
   <shar:wsVersion>2.0.0</shar:wsVersion>
   <shar:id>{escape(pid)}</shar:id>
   <shar:password>{escape(pw)}</shar:password>
   <shar:productId>{escape(style)}</shar:productId>
  </ns:GetInventoryLevelsRequest>
 </soapenv:Body>
</soapenv:Envelope>"""
    try:
        status, text = _call(INVENTORY_ENDPOINT, "getInventoryLevels", body)
    except requests.RequestException as exc:
        print(f"  {style}: REQUEST FAILED: {str(exc)[:140]}")
        return
    codes = _tag(text, "code")
    if codes or "Fault" in text[:2000]:
        descs = _tag(text, "description")
        print(f"  {style}: HTTP {status}; service message: "
              f"{codes[:1]} {descs[:1] or text[:160]!r}")
        return
    qtys = [int(q) for q in _tag(text, "value") if q.strip().isdigit()]
    over_cap = [q for q in qtys if q > 1500]
    print(f"  {style}: HTTP {status}  {len(qtys)} quantity value(s); "
          f"max={max(qtys) if qtys else '-'}; "
          f"{len(over_cap)} value(s) ABOVE the 1500 FTP cap"
          f"{' <-- UNCAPPED CONFIRMED' if over_cap else ''}")


def main() -> int:
    pid, pw, which = _creds()
    if not pid or not pw:
        print("no credentials available (need SANMAR_WS_ID/SANMAR_WS_PASSWORD "
              "or the SFTP pair) -- cannot probe")
        return 1
    print(f"ws base: {WS_BASE}")
    print(f"auth: trying {which}\n")

    print("=" * 70)
    print("1. Product Data 2.0.0 -- does isCloseout come through?")
    print("=" * 70)
    for style in PROBE_STYLES:
        probe_product(pid, pw, style.strip())

    print()
    print("=" * 70)
    print("2. Inventory 2.0.0 -- is inventory uncapped here?")
    print("=" * 70)
    for style in PROBE_STYLES:
        probe_inventory(pid, pw, style.strip())

    print("\nIf auth failed above, ask SanMar Integration Support to enable "
          "PromoStandards web services\nfor the account (same team that "
          "provisions FTP) -- sanmarintegrations@sanmar.com.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""UA pricing probe (DC OneSource / PromoStandards PPC 1.0.0), read-only.

Confirms the Pricing & Configuration service is enabled for our key and
shows what Net (our cost) and List (MSRP) prices look like for one style,
before wiring pricing into ua_backfill.py. Sequence per the PromoStandards
spec: getFobPoints (pricing requests require a fobId), then
getConfigurationAndPricing once per price type.

Env: DCOS_KEY_ID / DCOS_KEY_PASSWORD; UA_PROBE_STYLE overrides the style.
"""

from __future__ import annotations

import os
import re

import requests

BASE = "https://api.dc-onesource.com/xml/UNDERARMOR"
PPC_NS = "http://www.promostandards.org/WSDL/PricingAndConfiguration/1.0.0/"


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
    return resp.text


def get_fob_points(key_id: str, key_pw: str, style: str) -> str:
    body = (
        f'<ns:GetFobPointsRequest xmlns:ns="{PPC_NS}" '
        f'xmlns:shar="{PPC_NS}SharedObjects/">'
        f"<shar:wsVersion>1.0.0</shar:wsVersion><shar:id>{key_id}</shar:id>"
        f"<shar:password>{key_pw}</shar:password>"
        f"<shar:productId>{style}</shar:productId>"
        "<shar:localizationCountry>US</shar:localizationCountry>"
        "<shar:localizationLanguage>en</shar:localizationLanguage>"
        "</ns:GetFobPointsRequest>"
    )
    return _soap(f"{BASE}/PPC/1.0.0/soap", "getFobPoints", body)


def get_pricing(key_id: str, key_pw: str, style: str, fob_id: str, price_type: str) -> str:
    body = (
        f'<ns:GetConfigurationAndPricingRequest xmlns:ns="{PPC_NS}" '
        f'xmlns:shar="{PPC_NS}SharedObjects/">'
        f"<shar:wsVersion>1.0.0</shar:wsVersion><shar:id>{key_id}</shar:id>"
        f"<shar:password>{key_pw}</shar:password>"
        f"<shar:productId>{style}</shar:productId>"
        "<shar:currency>USD</shar:currency>"
        f"<shar:fobId>{fob_id}</shar:fobId>"
        f"<shar:priceType>{price_type}</shar:priceType>"
        "<shar:localizationCountry>US</shar:localizationCountry>"
        "<shar:localizationLanguage>en</shar:localizationLanguage>"
        "<shar:configurationType>Blank</shar:configurationType>"
        "</ns:GetConfigurationAndPricingRequest>"
    )
    return _soap(f"{BASE}/PPC/1.0.0/soap", "getConfigurationAndPricing", body)


def _err(text: str) -> str | None:
    m = re.search(r"<.*?(?:ErrorMessage|Fault).*?>.*?</.*?(?:ErrorMessage|Fault).*?>", text, re.S)
    return m.group(0)[:600] if m else None


def main() -> int:
    key_id = os.environ["DCOS_KEY_ID"]
    key_pw = os.environ["DCOS_KEY_PASSWORD"]
    style = os.environ.get("UA_PROBE_STYLE", "")
    if not style:
        from ua_backfill import get_sellable_styles  # scripts/ is on sys.path

        styles = get_sellable_styles(key_id, key_pw)
        print(f"UA sellable styles: {len(styles):,}; probing the first")
        if not styles:
            print("no sellable styles returned")
            return 1
        style = styles[0]

    print(f"--- getFobPoints for style {style} ---")
    text = get_fob_points(key_id, key_pw, style)
    print(f"response bytes: {len(text):,}")
    err = _err(text)
    if err:
        print(err)
    fob_ids = re.findall(r"<\s*(?:\w+:)?fobId\s*>([^<]+)<", text)
    print(f"fobIds: {fob_ids[:5]}")
    if not fob_ids:
        print("no fobId found -- PPC may be disabled for this key/style")
        return 1

    for price_type in ("Net", "List", "Customer"):
        print(f"\n--- getConfigurationAndPricing priceType={price_type} ---")
        text = get_pricing(key_id, key_pw, style, fob_ids[0], price_type)
        print(f"response bytes: {len(text):,}")
        err = _err(text)
        if err:
            print(err)
            continue
        parts = re.findall(r"<\s*(?:\w+:)?partId\s*>([^<]+)<", text)
        prices = re.findall(r"<\s*(?:\w+:)?price\s*>([^<]+)<", text)
        print(f"parts: {len(parts)}  prices: {len(prices)}")
        for p, pr in list(zip(parts, prices))[:5]:
            print(f"  {p}: {pr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

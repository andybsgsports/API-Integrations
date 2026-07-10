"""DC OneSource (PromoStandards) auth probe — Under Armour, read-only.

Calls Product Data 2.0.0 ``getProductSellable`` with the DC OneSource key
pair; on success reports how many sellable product/part rows come back.
PromoStandards carries credentials in the request body, so this both tests
auth and confirms data access. Env: DCOS_KEY_ID / DCOS_KEY_PASSWORD.
"""

from __future__ import annotations

import os
import re

import requests

PRODUCT_URL = "https://api.dc-onesource.com/xml/UNDERARMOR/Product/2.0.0/soap"

ENVELOPE = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
    xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:ns="http://www.promostandards.org/WSDL/ProductDataService/2.0.0/"
    xmlns:shar="http://www.promostandards.org/WSDL/ProductDataService/2.0.0/SharedObjects/">
  <soapenv:Header/>
  <soapenv:Body>
    <ns:GetProductSellableRequest>
      <shar:wsVersion>2.0.0</shar:wsVersion>
      <shar:id>{key_id}</shar:id>
      <shar:password>{key_pw}</shar:password>
      <shar:localizationCountry>US</shar:localizationCountry>
      <shar:localizationLanguage>en</shar:localizationLanguage>
      <ns:isSellable>true</ns:isSellable>
    </ns:GetProductSellableRequest>
  </soapenv:Body>
</soapenv:Envelope>"""


def main() -> int:
    key_id = os.environ.get("DCOS_KEY_ID", "")
    key_pw = os.environ.get("DCOS_KEY_PASSWORD", "")
    if not key_id or not key_pw:
        print("DCOS_KEY_ID / DCOS_KEY_PASSWORD not set")
        return 1
    resp = requests.post(
        PRODUCT_URL,
        data=ENVELOPE.format(key_id=key_id, key_pw=key_pw).encode(),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "getProductSellable",
        },
        timeout=300,
    )
    text = resp.text
    print(f"HTTP {resp.status_code}; response bytes: {len(text):,}")
    if "ErrorMessage" in text or resp.status_code != 200:
        # print the error block only — never the request (it carries the key)
        m = re.search(r"<.*?ErrorMessage.*?>.*?</.*?ErrorMessage.*?>", text, re.S)
        print(m.group(0)[:500] if m else text[:500])
        return 1
    products = len(re.findall(r"<\s*(?:\w+:)?productId\s*>", text))
    parts = len(re.findall(r"<\s*(?:\w+:)?partId\s*>", text))
    print(f"AUTH OK — sellable rows: productId tags={products:,} partId tags={parts:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

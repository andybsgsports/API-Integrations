"""Under Armour (DC OneSource) reconcile probe — read-only.

Pulls the sellable product list via PromoStandards and measures how UA's
style numbers line up with existing NetSuite items' Vendor Name/Code. Env:
DCOS_KEY_ID / DCOS_KEY_PASSWORD (+ NetSuite creds via .env).
"""

from __future__ import annotations

import os
import re

import requests

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape

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
    key_id = os.environ["DCOS_KEY_ID"]
    key_pw = os.environ["DCOS_KEY_PASSWORD"]
    resp = requests.post(
        PRODUCT_URL,
        data=ENVELOPE.format(key_id=key_id, key_pw=key_pw).encode(),
        headers={"Content-Type": "text/xml; charset=utf-8",
                 "SOAPAction": "getProductSellable"},
        timeout=600,
    )
    resp.raise_for_status()
    text = resp.text
    products = sorted(set(re.findall(r"<\s*(?:\w+:)?productId\s*>([^<]+)<", text)))
    parts = set(re.findall(r"<\s*(?:\w+:)?partId\s*>([^<]+)<", text))
    print(f"UA sellable: {len(products):,} distinct styles / {len(parts):,} distinct parts")
    print(f"sample styles: {products[:8]}")

    client = NetSuiteClient(get_config().netsuite)
    hits = 0
    for i in range(0, len(products), 300):
        chunk = products[i : i + 300]
        in_list = ", ".join(f"'{_sql_escape(v)}'" for v in chunk)
        rows = client.suiteql(
            f"SELECT DISTINCT vendorname FROM item WHERE vendorname IN ({in_list})"
        )
        hits += len(rows)
    pct = 100.0 * hits / len(products) if products else 0.0
    print(f"UA styles present as Vendor Name/Code on existing items: "
          f"{hits:,}/{len(products):,} ({pct:.1f}%)")
    print("(read-only probe; no writes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

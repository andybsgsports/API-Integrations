"""Single-field live test: can we set a custom item field's Description via
SOAP, referencing the record by scriptId (no internalId lookup needed)?
Targets custitem_sanmar_gtin -- the exact field the user showed a screenshot
of -- and reads it back via SOAP get to confirm the write landed.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

from sanmar_netsuite.config import get_config
from ns_soap import VERSION, is_success, post

FIELD = "custitem_sanmar_gtin"
DESCRIPTION = (
    "This item's barcode (GTIN/UPC) as provided by SanMar's product feed."
)


def get_field(cfg, scriptid: str) -> str:
    body = f"""
    <platformMsgs:get>
      <platformMsgs:baseRef xsi:type="platformCore:CustomizationRef"
          scriptId="{scriptid}" type="itemCustomField"/>
    </platformMsgs:get>"""
    return post(cfg, "get", body)


def update_description(cfg, internal_id: str, description: str) -> str:
    ns = f"urn:customization_{VERSION}.setup.webservices.netsuite.com"
    body = f"""
    <platformMsgs:update xmlns:setupCustom="{ns}">
      <platformMsgs:record xsi:type="setupCustom:ItemCustomField" internalId="{internal_id}">
        <setupCustom:description>{escape(description)}</setupCustom:description>
      </platformMsgs:record>
    </platformMsgs:update>"""
    return post(cfg, "update", body)


def main() -> int:
    cfg = get_config().netsuite

    print(f"--- get: resolving {FIELD} by scriptId (read-only) ---")
    text = get_field(cfg, FIELD)
    print(text[:3000])
    if not is_success(text):
        print(f"\nget FAILED for {FIELD} -- cannot resolve internalId")
        return 1
    m = re.search(r'internalId="(\d+)"', text)
    if not m:
        print("\nget succeeded but no internalId found in response")
        return 1
    internal_id = m.group(1)
    print(f"\nresolved internalId: {internal_id}")

    print(f"\n--- update: setting description on {FIELD} (internalId {internal_id}) ---")
    text = update_description(cfg, internal_id, DESCRIPTION)
    ok = is_success(text)
    print(f"update {'succeeded' if ok else 'FAILED'}")
    if not ok:
        print("---- raw SOAP response (first 3000 chars) ----")
        print(text[:3000])
        return 1

    print(f"\n--- get: reading back {FIELD} ---")
    text = get_field(cfg, FIELD)
    print(text[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

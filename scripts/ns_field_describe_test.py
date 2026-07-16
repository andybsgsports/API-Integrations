"""Single-field live test: can we set a custom item field's Description via
SOAP, referencing the record by scriptId (no internalId lookup needed)?
Targets custitem_sanmar_gtin -- the exact field the user showed a screenshot
of -- and reads it back via SOAP get to confirm the write landed.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from sanmar_netsuite.config import get_config
from ns_soap import VERSION, is_success, post

FIELD = "custitem_sanmar_gtin"
DESCRIPTION = (
    "This item's barcode (GTIN/UPC) as provided by SanMar's product feed."
)


def _sid_suffix(scriptid: str) -> str:
    return scriptid.removeprefix("custitem")


def update_description(cfg, scriptid: str, description: str) -> str:
    ns = f"urn:customization_{VERSION}.setup.webservices.netsuite.com"
    body = f"""
    <platformMsgs:update xmlns:setupCustom="{ns}">
      <platformMsgs:record xsi:type="setupCustom:ItemCustomField" scriptId="{_sid_suffix(scriptid)}">
        <setupCustom:description>{escape(description)}</setupCustom:description>
      </platformMsgs:record>
    </platformMsgs:update>"""
    return post(cfg, "update", body)


def get_field(cfg, scriptid: str) -> str:
    body = f"""
    <platformMsgs:get>
      <platformMsgs:baseRef xsi:type="platformCore:CustomizationRef"
          scriptId="{scriptid}" type="itemCustomField"/>
    </platformMsgs:get>"""
    return post(cfg, "get", body)


def main() -> int:
    cfg = get_config().netsuite

    print(f"--- update: setting description on {FIELD} ---")
    text = update_description(cfg, FIELD, DESCRIPTION)
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

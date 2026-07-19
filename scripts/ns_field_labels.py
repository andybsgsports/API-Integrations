"""Enforce the display *label* on each per-warehouse custom field, via SOAP.

The individual warehouse fields were created with the S&S/SanMar warehouse
*code* as their label (``S&S Qty: IL``). The user wants them spelled out
(``S&S Qty: Lockport, IL``). The desired labels live in
``warehouse_fields.py``; this script reads each field's current label with a
read-only get-by-scriptId and updates only the ones that differ.

REST 404s on itemcustomfield, so this uses the same SOAP path as
``ns_field_describe_all.py``. The update is a merge -- only ``label`` is
sent, so description/help/displayType/subtab are untouched.

Read-only unless SYNC_DRY_RUN=false.
"""

from __future__ import annotations

import os
import re
from xml.sax.saxutils import escape, unescape

from ns_soap import VERSION, is_success, post
from warehouse_fields import SANMAR_WHSE_FIELDS, SS_WHSE_FIELDS

from sanmar_netsuite.config import get_config

# Non-warehouse fields whose live label needs correcting.
EXTRA_LABELS: dict[str, str] = {
    # holds the BACK image (front is on the main Item Image field)
    "custitem_mtec_front_image_url": "Momentec Back Image URL",
}

# scriptid -> desired label, across both supplier warehouse sets + extras.
DESIRED: dict[str, str] = {
    sid: label for sid, label in SS_WHSE_FIELDS.values()
} | {
    sid: label for sid, label in SANMAR_WHSE_FIELDS.values()
} | EXTRA_LABELS


def get_field(cfg, scriptid: str) -> str:
    body = f"""
    <platformMsgs:get>
      <platformMsgs:baseRef xsi:type="platformCore:CustomizationRef"
          scriptId="{scriptid}" type="itemCustomField"/>
    </platformMsgs:get>"""
    return post(cfg, "get", body)


def update_label(cfg, internal_id: str, label: str) -> str:
    ns = f"urn:customization_{VERSION}.setup.webservices.netsuite.com"
    body = f"""
    <platformMsgs:update xmlns:setupCustom="{ns}">
      <platformMsgs:record xsi:type="setupCustom:ItemCustomField" internalId="{internal_id}">
        <setupCustom:label>{escape(label)}</setupCustom:label>
      </platformMsgs:record>
    </platformMsgs:update>"""
    return post(cfg, "update", body)


def _tag_value(text: str, tag: str) -> str:
    m = re.search(rf"<setupCustom:{tag}>(.*?)</setupCustom:{tag}>", text)
    return unescape(m.group(1)) if m else ""


def main() -> int:
    cfg = get_config().netsuite
    allow_write = (os.environ.get("SYNC_DRY_RUN") or "true").lower() != "true"

    updated = unchanged = not_found = failures = 0
    for scriptid, label in sorted(DESIRED.items()):
        text = get_field(cfg, scriptid)
        if not is_success(text):
            not_found += 1
            print(f"  NOT FOUND: {scriptid}")
            continue
        m = re.search(r'internalId="(\d+)"', text)
        if not m:
            not_found += 1
            print(f"  no internalId in get response for {scriptid}")
            continue
        internal_id = m.group(1)
        cur_label = _tag_value(text, "label")
        if cur_label == label:
            unchanged += 1
            continue
        if not allow_write:
            updated += 1
            print(f"  WOULD relabel {scriptid} (id {internal_id}): "
                  f"{cur_label!r} -> {label!r}")
            continue
        result = update_label(cfg, internal_id, label)
        if is_success(result):
            updated += 1
            print(f"  relabeled {scriptid} (id {internal_id}): "
                  f"{cur_label!r} -> {label!r}")
        else:
            failures += 1
            print(f"  FAILED {scriptid} (id {internal_id}): {result[:300]}")

    verb = "relabeled" if allow_write else "WOULD relabel (dry run)"
    print(
        f"\nwarehouse field labels: {verb} {updated}; unchanged: {unchanged}; "
        f"not found: {not_found}; failures: {failures}"
    )
    return 1 if (failures or not_found) else 0


if __name__ == "__main__":
    raise SystemExit(main())

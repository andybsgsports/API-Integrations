"""Enforce metadata on every custitem_* field this project writes, via
SOAP (REST 404s on itemcustomfield entirely). Runs on CI.

Two properties are kept in line for each field:
* Description -- the Field Help text (from field_descriptions.py).
* Display Type -- "Inline Text": these fields are feed-managed, so they
  render as read-only text on item records instead of editable inputs.

For each field: resolve its internalId with a read-only get-by-scriptId,
then update only if something differs. Small, idempotent, metadata-only.
"""

from __future__ import annotations

import os
import re
from xml.sax.saxutils import escape

from field_descriptions import DESCRIPTIONS
from sanmar_netsuite.config import get_config
from ns_soap import VERSION, is_success, post


def get_field(cfg, scriptid: str) -> str:
    body = f"""
    <platformMsgs:get>
      <platformMsgs:baseRef xsi:type="platformCore:CustomizationRef"
          scriptId="{scriptid}" type="itemCustomField"/>
    </platformMsgs:get>"""
    return post(cfg, "get", body)


DISPLAY_TYPE = "_inlineText"


def update_field(cfg, internal_id: str, description: str, display_type: str) -> str:
    ns = f"urn:customization_{VERSION}.setup.webservices.netsuite.com"
    body = f"""
    <platformMsgs:update xmlns:setupCustom="{ns}">
      <platformMsgs:record xsi:type="setupCustom:ItemCustomField" internalId="{internal_id}">
        <setupCustom:description>{escape(description)}</setupCustom:description>
        <setupCustom:displayType>{display_type}</setupCustom:displayType>
      </platformMsgs:record>
    </platformMsgs:update>"""
    return post(cfg, "update", body)


def _tag_value(get_response_text: str, tag: str) -> str:
    m = re.search(rf"<setupCustom:{tag}>(.*?)</setupCustom:{tag}>", get_response_text)
    return m.group(1) if m else ""


def main() -> int:
    cfg = get_config().netsuite
    allow_write = (os.environ.get("SYNC_DRY_RUN") or "true").lower() != "true"

    updated = unchanged = not_found = failures = 0
    for scriptid, description in sorted(DESCRIPTIONS.items()):
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
        cur_desc = _tag_value(text, "description")
        cur_display = _tag_value(text, "displayType")
        diffs = []
        if cur_desc != description:
            diffs.append("description")
        if cur_display != DISPLAY_TYPE:
            diffs.append(f"displayType {cur_display or '?'} -> {DISPLAY_TYPE}")
        if not diffs:
            unchanged += 1
            continue
        if not allow_write:
            updated += 1
            print(f"  WOULD update {scriptid} (id {internal_id}): {', '.join(diffs)}")
            continue
        result = update_field(cfg, internal_id, description, DISPLAY_TYPE)
        if is_success(result):
            updated += 1
            print(f"  updated {scriptid} (id {internal_id}): {', '.join(diffs)}")
        else:
            failures += 1
            print(f"  FAILED {scriptid} (id {internal_id}): {result[:300]}")

    verb = "updated" if allow_write else "WOULD update (dry run)"
    print(
        f"\nfield descriptions: {verb} {updated}; unchanged: {unchanged}; "
        f"not found: {not_found}; failures: {failures}"
    )
    return 1 if (failures or not_found) else 0


if __name__ == "__main__":
    raise SystemExit(main())

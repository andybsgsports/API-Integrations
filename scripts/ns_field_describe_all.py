"""Set the Description on every custitem_* field this project writes, via
SOAP (REST 404s on itemcustomfield entirely). Runs on CI.

For each field: resolve its internalId with a read-only get-by-scriptId,
then update the description if it doesn't already match. Small, one-shot,
idempotent -- no dry-run/smoke ladder needed at this scale (53 fields,
metadata only, each write independently reversible).
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


def update_description(cfg, internal_id: str, description: str) -> str:
    ns = f"urn:customization_{VERSION}.setup.webservices.netsuite.com"
    body = f"""
    <platformMsgs:update xmlns:setupCustom="{ns}">
      <platformMsgs:record xsi:type="setupCustom:ItemCustomField" internalId="{internal_id}">
        <setupCustom:description>{escape(description)}</setupCustom:description>
      </platformMsgs:record>
    </platformMsgs:update>"""
    return post(cfg, "update", body)


def _current_description(get_response_text: str) -> str:
    m = re.search(
        r"<setupCustom:description>(.*?)</setupCustom:description>", get_response_text
    )
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
        current = _current_description(text)
        if current == description:
            unchanged += 1
            continue
        if not allow_write:
            updated += 1
            print(f"  WOULD update {scriptid} (id {internal_id})")
            continue
        result = update_description(cfg, internal_id, description)
        if is_success(result):
            updated += 1
            print(f"  updated {scriptid} (id {internal_id})")
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

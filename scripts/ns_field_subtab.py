"""Put the per-warehouse fields on the same subtab as the text-blob field (SOAP).

The individual warehouse fields (custitem_ss_qty_* / custitem_sanmar_qty_*)
exist and carry data but aren't on the item form. The "* Qty By Warehouse"
text field (custitem_ss_qty_by_whse / custitem_sanmar_qty_by_whse) IS on the
form -- under the "Vendor Inventory Levels" subtab the user created. So: read
that field's subtab id, then set the same subtab on every warehouse field, so
they surface next to it.

Read-only probe unless SYNC_DRY_RUN=false. SOAP update is a merge -- only the
subtab is changed; description/help/displayType are untouched.
"""

from __future__ import annotations

import os
import re

from ns_soap import VERSION, is_success, post
from warehouse_fields import SANMAR_QTY_FIELDS, SS_QTY_FIELDS

from sanmar_netsuite.config import get_config

# (text-blob field whose subtab we copy) -> [warehouse fields to move onto it]
GROUPS = {
    "custitem_ss_qty_by_whse": SS_QTY_FIELDS,
    "custitem_sanmar_qty_by_whse": SANMAR_QTY_FIELDS,
}


def get_field(cfg, scriptid: str) -> str:
    body = f"""
    <platformMsgs:get>
      <platformMsgs:baseRef xsi:type="platformCore:CustomizationRef"
          scriptId="{scriptid}" type="itemCustomField"/>
    </platformMsgs:get>"""
    return post(cfg, "get", body)


def subtab_ref(get_text: str) -> tuple[str, str] | None:
    """Return (internalId, type) of the field's subtab, or None."""
    m = re.search(r"<(?:\w+:)?subtab\b([^>]*)/?>", get_text)
    if not m:
        m = re.search(r"<(?:\w+:)?subtab\b([^>]*)>", get_text)
    if not m:
        return None
    attrs = m.group(1)
    iid = re.search(r'internalId="(\d+)"', attrs)
    typ = re.search(r'type="([^"]+)"', attrs)
    return (iid.group(1), typ.group(1) if typ else "subtab") if iid else None


def internal_id(get_text: str) -> str | None:
    m = re.search(r'internalId="(\d+)"', get_text)
    return m.group(1) if m else None


def set_subtab(cfg, field_internal_id: str, subtab_id: str, subtab_type: str) -> str:
    ns = f"urn:customization_{VERSION}.setup.webservices.netsuite.com"
    body = f"""
    <platformMsgs:update xmlns:setupCustom="{ns}">
      <platformMsgs:record xsi:type="setupCustom:ItemCustomField" internalId="{field_internal_id}">
        <setupCustom:subtab internalId="{subtab_id}" type="{subtab_type}"/>
      </platformMsgs:record>
    </platformMsgs:update>"""
    return post(cfg, "update", body)


def main() -> int:
    cfg = get_config().netsuite
    allow_write = (os.environ.get("SYNC_DRY_RUN") or "true").lower() != "true"

    updated = unchanged = failures = 0
    for anchor, wh_fields in GROUPS.items():
        text = get_field(cfg, anchor)
        if not is_success(text):
            print(f"anchor {anchor}: GET failed -- skipping its group")
            print(text[:400])
            failures += 1
            continue
        ref = subtab_ref(text)
        if not ref:
            print(f"anchor {anchor}: no <subtab> in its definition "
                  "(is it actually on a subtab?) -- skipping")
            failures += 1
            continue
        subtab_id, subtab_type = ref
        print(f"\nanchor {anchor}: subtab internalId={subtab_id} type={subtab_type}")

        for sid in wh_fields:
            ftext = get_field(cfg, sid)
            if not is_success(ftext):
                print(f"  {sid}: GET failed -- skip")
                failures += 1
                continue
            cur = subtab_ref(ftext)
            if cur and cur[0] == subtab_id:
                unchanged += 1
                continue
            fid = internal_id(ftext)
            if not fid:
                print(f"  {sid}: no internalId -- skip")
                failures += 1
                continue
            if not allow_write:
                updated += 1
                print(f"  WOULD move {sid} (id {fid}) -> subtab {subtab_id}")
                continue
            res = set_subtab(cfg, fid, subtab_id, subtab_type)
            if is_success(res):
                updated += 1
                print(f"  moved {sid} (id {fid}) -> subtab {subtab_id}")
            else:
                failures += 1
                snippet = res[res.find("message"): res.find("message") + 160]
                print(f"  FAILED {sid}: {snippet}")

    verb = "moved" if allow_write else "WOULD move (dry run)"
    print(f"\nsubtab assignment: {verb} {updated}; already there: {unchanged}; "
          f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

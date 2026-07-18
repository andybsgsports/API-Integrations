"""Set the Maximum Value constraint on each per-warehouse qty field (SOAP).

The per-warehouse fields are Integer custom fields. NetSuite integer fields
carry a numeric ``maxValue`` (and ``minValue``) constraint on the field
definition; a value above the cap is rejected on write. The user wants the
cap raised to 50000 so large DC quantities always land.

Mirrors ns_field_labels.py: read-only get-by-scriptId, diff-aware update of
only the fields whose maxValue differs. The update is a merge -- only
maxValue is sent, so label/description/help/displayType are untouched.

In dry-run it also dumps the numeric-constraint slice of the first field so
the exact current min/max is visible. Read-only unless SYNC_DRY_RUN=false.

Env: MAX_VALUE (default 50000).
"""

from __future__ import annotations

import os
import re

from ns_soap import VERSION, is_success, post
from warehouse_fields import SANMAR_WHSE_FIELDS, SS_WHSE_FIELDS

from sanmar_netsuite.config import get_config

# Per-warehouse integer fields -> capped at MAX_VALUE (default 50000, the
# same ceiling S&S itself displays as ">50,000"). The aggregate "QTY
# AVAILABLE" totals sum every warehouse, so they get a much higher ceiling
# (AVAIL_MAX_VALUE) or they'd clamp too.
QTY_FIELDS: list[str] = (
    [sid for sid, _ in SS_WHSE_FIELDS.values()]
    + [sid for sid, _ in SANMAR_WHSE_FIELDS.values()]
)
AVAIL_FIELDS: list[str] = [
    "custitem_ss_qty_available",
    "custitem_sanmar_qty_available",
]


def get_field(cfg, scriptid: str) -> str:
    body = f"""
    <platformMsgs:get>
      <platformMsgs:baseRef xsi:type="platformCore:CustomizationRef"
          scriptId="{scriptid}" type="itemCustomField"/>
    </platformMsgs:get>"""
    return post(cfg, "get", body)


def set_maxvalue(cfg, internal_id: str, max_value: int) -> str:
    ns = f"urn:customization_{VERSION}.setup.webservices.netsuite.com"
    body = f"""
    <platformMsgs:update xmlns:setupCustom="{ns}">
      <platformMsgs:record xsi:type="setupCustom:ItemCustomField" internalId="{internal_id}">
        <setupCustom:maxValue>{max_value}</setupCustom:maxValue>
      </platformMsgs:record>
    </platformMsgs:update>"""
    return post(cfg, "update", body)


def _tag_value(text: str, tag: str) -> str:
    m = re.search(rf"<setupCustom:{tag}>(.*?)</setupCustom:{tag}>", text)
    return m.group(1) if m else ""


def _num_equal(cur: str, want: int) -> bool:
    if not cur:
        return False
    try:
        return int(float(cur)) == want
    except ValueError:
        return False


def main() -> int:
    cfg = get_config().netsuite
    allow_write = (os.environ.get("SYNC_DRY_RUN") or "true").lower() != "true"
    max_value = int(os.environ.get("MAX_VALUE") or "50000")
    avail_max = int(os.environ.get("AVAIL_MAX_VALUE") or "5000000")
    print(f"target maxValue = {max_value} (per-warehouse); "
          f"{avail_max} (aggregate available)")

    # scriptid -> its target ceiling
    targets: dict[str, int] = {sid: max_value for sid in QTY_FIELDS}
    targets.update({sid: avail_max for sid in AVAIL_FIELDS})

    updated = unchanged = not_found = failures = 0
    dumped = False
    for scriptid in sorted(targets):
        target = targets[scriptid]
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
        if not dumped:
            cur_min = _tag_value(text, "minValue")
            cur_max = _tag_value(text, "maxValue")
            print(f"  [sample {scriptid}] minValue={cur_min or '(none)'} "
                  f"maxValue={cur_max or '(none)'}")
            dumped = True
        cur = _tag_value(text, "maxValue")
        if _num_equal(cur, target):
            unchanged += 1
            continue
        if not allow_write:
            updated += 1
            print(f"  WOULD set maxValue {scriptid} (id {internal_id}): "
                  f"{cur or '(none)'} -> {target}")
            continue
        result = set_maxvalue(cfg, internal_id, target)
        if is_success(result):
            updated += 1
            print(f"  set maxValue {scriptid} (id {internal_id}): "
                  f"{cur or '(none)'} -> {target}")
        else:
            failures += 1
            print(f"  FAILED {scriptid} (id {internal_id}): {result[:300]}")

    verb = "set" if allow_write else "WOULD set (dry run)"
    print(
        f"\nmaxValue: {verb} {updated}; unchanged: {unchanged}; "
        f"not found: {not_found}; failures: {failures}"
    )
    return 1 if (failures or not_found) else 0


if __name__ == "__main__":
    raise SystemExit(main())

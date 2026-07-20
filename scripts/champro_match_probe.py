"""Diagnose why a specific Champro item didn't get feed data/images.

For each needle (e.g. B040, B041): is it a sellable feed style, which sellable
styles expand to cover it, what parts/GTINs does getProduct return, and what
does the matching NetSuite item look like (vendorname, upccode, champro part
id). Read-only. Set CHAMPRO_PROBE to a comma list (default 'B040,B041').
"""

from __future__ import annotations

import os

from dcos_backfill import (
    SUPPLIERS,
    expand_style_members,
    get_parts,
    get_sellable_styles,
)

from sanmar_netsuite.config import get_config as ns_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape


def main() -> int:
    key_id = os.environ["DCOS_KEY_ID"]
    key_pw = os.environ["DCOS_KEY_PASSWORD"]
    needles = [s.strip().upper()
               for s in (os.environ.get("CHAMPRO_PROBE") or "B040,B041").split(",")
               if s.strip()]
    base = f"https://api.dc-onesource.com/xml/{SUPPLIERS['champro']['slug']}"

    styles = get_sellable_styles(base, key_id, key_pw)
    print(f"champro sellable styles: {len(styles):,}")
    direct = [s for s in styles if s.upper() in needles]
    print(f"sellable styles that ARE a needle: {direct}")
    substr = [s for s in styles if any(n in s.upper() for n in needles)]
    print(f"sellable styles CONTAINING a needle (substring): {substr}")
    covering = [s for s in styles
                if any(n in expand_style_members(s) for n in needles)]
    print(f"sellable styles whose expanded members COVER a needle: {covering}")

    for s in (substr or covering)[:6]:
        print(f"\n--- getProduct({s})  members={expand_style_members(s)} ---")
        try:
            parts = get_parts(base, key_id, key_pw, s)
        except Exception as exc:  # noqa: BLE001
            print(f"  getProduct failed: {str(exc)[:150]}")
            continue
        print(f"  {len(parts)} part(s):")
        for p in parts[:12]:
            print(f"    partId={p.get('partId')!r} gtin={p.get('gtin')!r} "
                  f"colors={p.get('colors')} sizes={p.get('sizes')}")

    client = NetSuiteClient(ns_config().netsuite)
    for n in needles:
        rows = client.suiteql(
            "SELECT id, itemid, vendorname, upccode, custitem_champro_part_id AS pid "
            f"FROM item WHERE UPPER(vendorname) = '{_sql_escape(n)}' "
            f"OR UPPER(itemid) = '{_sql_escape(n)}'"
        )
        print(f"\nNetSuite items for {n!r}: "
              + str([(r.get("id"), r.get("itemid"), r.get("vendorname"),
                      r.get("upccode"), r.get("pid")) for r in rows]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

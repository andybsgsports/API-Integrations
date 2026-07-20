"""Dump the raw S&S /Inventory warehouse entries to learn code -> full name.

The per-warehouse fields are labelled with S&S's 2-letter codes (IL, KS, …);
the user wants them spelled out (Bolingbrook IL, Olathe KS, …). S&S's own
/Inventory response carries each warehouse's full name/city next to its
abbreviation, so print every field of a few warehouse rows and the distinct
abbr -> name mapping across a batch of SKUs.

Env: SS_PROBE_SKUS (comma list; default a few from a well-stocked style).
"""

from __future__ import annotations

import json
import os

from ss_activewear_netsuite.config import get_config as ss_config
from ss_activewear_netsuite.ss_activewear.client import SsClient


def main() -> int:
    ss = SsClient(ss_config().ss_api)
    # a well-stocked style (Gildan 8000) so most warehouses report a row
    skus = os.environ.get("SS_PROBE_SKUS", "").strip()
    sku_list = [s.strip() for s in skus.split(",") if s.strip()]
    if not sku_list:
        # pull the first style's SKUs
        prods = list(ss.iter_products(style_id=os.environ.get("SS_PROBE_STYLE", "39")))
        sku_list = [str(p.sku) for p in prods[:5] if p.sku]
    print(f"probing SKUs: {sku_list}")

    mapping: dict[str, set] = {}
    first_dump = True
    for sku in sku_list:
        raw = ss._get(f"/Inventory/{sku}")
        rows = raw if isinstance(raw, list) else [raw]
        for r in rows:
            whs = r.get("warehouses") or r.get("warehouseAvailability") or []
            if first_dump and whs:
                print("\n=== raw warehouse entry (all fields) ===")
                print(json.dumps(whs[0], indent=2, default=str))
                if len(whs) > 1:
                    print(json.dumps(whs[1], indent=2, default=str))
                first_dump = False
            for w in whs:
                abbr = str(w.get("warehouseAbbr") or w.get("warehouse") or "").strip()
                # collect any name-ish fields
                name_bits = []
                for k in ("warehouseName", "name", "city", "state", "warehouse"):
                    v = w.get(k)
                    if v:
                        name_bits.append(f"{k}={v}")
                if abbr:
                    mapping.setdefault(abbr, set()).update(name_bits)

    print("\n=== abbr -> name-ish fields seen ===")
    for abbr in sorted(mapping):
        print(f"  {abbr:<4} {sorted(mapping[abbr])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

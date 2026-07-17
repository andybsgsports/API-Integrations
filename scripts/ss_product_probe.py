"""Read-only probe: one item's custitem_ss_* sandbox values vs the RAW S&S
API JSON for the same SKU (runs on CI).

The user flagged S&S MAP Price (0.01), MSRP (blank), Case Size (blank) and
piece==dozen==case price on an item record as looking wrong. This prints,
side by side, (1) what the sandbox item carries, (2) the raw ``/Products``
JSON row S&S returns for that SKU, and (3) the products.json snapshot row --
so we can tell a feed quirk from a field-mapping bug.

Env: ``PROBE_ITEM_ID`` (NetSuite internal id, default 84171).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from ss_activewear_netsuite.config import get_config as ss_config
from ss_activewear_netsuite.ss_activewear.client import SsClient

SS_COLS = [
    "custitem_ss_sku", "custitem_ss_style", "custitem_ss_brand",
    "custitem_ss_map", "custitem_ss_msrp", "custitem_ss_piece_price",
    "custitem_ss_dozen_price", "custitem_ss_case_price",
    "custitem_ss_case_size", "custitem_ss_qty_available",
]


def main() -> int:
    item_id = os.environ.get("PROBE_ITEM_ID", "84171").strip()
    client = NetSuiteClient(get_config().netsuite)

    print("=== 0. image-field coverage across all matched S&S items ===")
    for col in ("custitem_ss_front_image_url", "custitem_ss_on_model_image_url"):
        n = client.suiteql(f"SELECT COUNT(*) AS n FROM item WHERE {col} IS NOT NULL")
        print(f"  {col:<36} populated on {n[0]['n']} items")
    n = client.suiteql(
        "SELECT COUNT(*) AS n FROM item WHERE custitem_ss_sku IS NOT NULL"
    )
    print(f"  (matched S&S items total: {n[0]['n']})")

    print(f"=== 1. sandbox item {item_id} ===")
    rows = client.suiteql(
        f"SELECT id, itemid, {', '.join(SS_COLS)} FROM item WHERE id = {int(item_id)}"
    )
    if not rows:
        print("item not found")
        return 1
    row = rows[0]
    for k in ["itemid", *SS_COLS]:
        print(f"  {k:<32} {row.get(k)!r}")

    sku = str(row.get("custitem_ss_sku") or "").strip()
    if not sku:
        print("item has no custitem_ss_sku -- cannot probe the S&S API")
        return 1

    ss = SsClient(ss_config().ss_api)
    print(f"\n=== 2. RAW S&S /Products/{sku} JSON ===")
    raw = ss._get(f"/Products/{sku}")
    print(json.dumps(raw, indent=2, default=str)[:4000])

    print(f"\n=== 3. products.json snapshot row for {sku} ===")
    snap = Path(ss_config().download_dir) / "products.json"
    if snap.exists():
        for p in json.loads(snap.read_text(encoding="utf-8")):
            if str(p.get("sku")) == sku:
                print(json.dumps(p, indent=2, default=str)[:2500])
                break
        else:
            print("SKU not in snapshot")
    else:
        print("no local products.json (run `ss-sync download` first)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

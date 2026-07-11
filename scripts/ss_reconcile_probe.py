"""Read-only probe: how would S&S SKUs match the existing NetSuite catalog?

Measures the two join strategies for adopting existing items (instead of
creating ``SS-`` duplicates):

* **GTIN -> upcCode** — S&S and SanMar both report the manufacturer barcode,
  and the SanMar back-fill stamps it onto matched items, so shared products
  should join on it exactly.
* **style -> vendorname** — how many S&S style names appear as a Vendor
  Name/Code on existing items (works for items the UPC back-fill missed).

Prints coverage stats only; makes **no writes**.
"""

from __future__ import annotations

import json
from pathlib import Path

from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape
from ss_activewear_netsuite.config import get_config


def _chunks(values: list[str], n: int = 300):
    for i in range(0, len(values), n):
        yield values[i : i + n]


def _in_list(values: list[str]) -> str:
    return ", ".join(f"'{_sql_escape(v)}'" for v in values)


def main() -> int:
    cfg = get_config()
    products_file = Path(cfg.download_dir) / "products.json"
    products = json.loads(products_file.read_text(encoding="utf-8"))
    print(f"Loaded {len(products)} S&S SKUs from {products_file}")

    gtins = sorted({p["gtin"] for p in products if p.get("gtin")})
    styles = sorted({p["style_name"] for p in products if p.get("style_name")})
    print(f"distinct GTINs: {len(gtins)}; distinct styles: {len(styles)}")

    client = NetSuiteClient(cfg.netsuite)

    gtin_hits = 0
    for chunk in _chunks(gtins):
        rows = client.suiteql(
            f"SELECT upccode FROM item WHERE upccode IN ({_in_list(chunk)})"
        )
        gtin_hits += len({str(r["upccode"]) for r in rows})

    style_hits = 0
    for chunk in _chunks(styles):
        rows = client.suiteql(
            "SELECT DISTINCT vendorname FROM item "
            f"WHERE vendorname IN ({_in_list(chunk)})"
        )
        style_hits += len(rows)

    pct_g = 100.0 * gtin_hits / len(gtins) if gtins else 0.0
    pct_s = 100.0 * style_hits / len(styles) if styles else 0.0
    print()
    print(f"GTIN -> upcCode: {gtin_hits}/{len(gtins)} GTINs on an existing item ({pct_g:.1f}%)")
    print(f"style -> vendorname: {style_hits}/{len(styles)} styles present ({pct_s:.1f}%)")
    print("\n(read-only probe; no writes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

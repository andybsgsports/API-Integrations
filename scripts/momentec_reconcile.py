"""Read-only Momentec → NetSuite reconciliation report (runs on CI).

Downloads the product feeds, matches every SKU to an existing item by
vendorname + matrix color/size option ids, and writes a mapping CSV artifact.
Env knob: ``RECONCILE_STYLE_LIMIT`` (0 = all styles).
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from urllib.request import Request, urlopen

from momentec_netsuite.adopt import match_momentec
from momentec_netsuite.config import get_config
from momentec_netsuite.feeds import parse_product_data
from sanmar_netsuite.config import get_config as ns_config
from sanmar_netsuite.netsuite.client import NetSuiteClient


def fetch(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=300) as resp, dest.open("wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    return dest


def main() -> int:
    cfg = get_config()
    limit = int(os.environ.get("RECONCILE_STYLE_LIMIT", "0") or "0")
    dl = Path(cfg.download_dir)
    products = fetch(cfg.products_url, dl / "product-data-std-all.csv")
    sub = fetch(cfg.sublimation_url, dl / "sublimation-product-data-std-all.csv")
    styles = parse_product_data([products, sub])
    print(f"Parsed {len(styles)} styles / {sum(len(s.skus) for s in styles)} SKUs")

    client = NetSuiteClient(ns_config().netsuite)
    report = match_momentec(client, styles, style_limit=limit)
    print("\n" + report.summary())

    out = Path("data/momentec_reconcile.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["item_sku", "style", "color", "size", "gtin", "upc",
                    "ns_id", "method"])
        for r in report.rows:
            w.writerow([r.item_sku, r.style, r.color, r.size, r.gtin, r.upc,
                        r.ns_id or "", r.method])
    print(f"Wrote mapping -> {out} ({len(report.rows)} rows)")

    unmatched = [r for r in report.rows if not r.ns_id][:12]
    if unmatched:
        print("\nSample unmatched (style | color | size):")
        for r in unmatched:
            print(f"  {r.style} | {r.color} | {r.size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

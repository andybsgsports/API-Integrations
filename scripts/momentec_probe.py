"""Momentec feed probe (runs on CI): download all four public feeds, parse,
and report shape + barcode coverage. If NetSuite credentials are present,
also report how many feed GTINs/UPCs already match an existing item's
upcCode — the adopt-don't-duplicate coverage metric. Read-only everywhere.
"""

from __future__ import annotations

from pathlib import Path
from urllib.request import Request, urlopen

from momentec_netsuite.config import get_config
from momentec_netsuite.feeds import parse_product_data


def fetch(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=300) as resp, dest.open("wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    print(f"downloaded {url} -> {dest} ({dest.stat().st_size:,} bytes)")
    return dest


def main() -> int:
    cfg = get_config()
    dl = Path(cfg.download_dir)
    products = fetch(cfg.products_url, dl / "product-data-std-all.csv")
    sub = fetch(cfg.sublimation_url, dl / "sublimation-product-data-std-all.csv")
    images = fetch(cfg.images_url, dl / "product-images-all.csv")
    inventory = fetch(cfg.inventory_url, dl / "ASG_inventory_data.csv")

    styles = parse_product_data([products, sub])
    total = sum(len(s.skus) for s in styles)
    gtins = {k.gtin for s in styles for k in s.skus if k.gtin}
    upcs = {k.upc for s in styles for k in s.skus if k.upc}
    print(f"\nstyles: {len(styles):,}  SKUs: {total:,}")
    print(f"distinct GTINs: {len(gtins):,}  distinct UPCs: {len(upcs):,}")
    print(f"images rows: {sum(1 for _ in images.open()) - 1:,}")
    print(f"inventory rows: {sum(1 for _ in inventory.open()) - 1:,}")

    # Optional: NetSuite-side barcode coverage (adopt metric)
    try:
        from sanmar_netsuite.config import get_config as ns_cfg
        from sanmar_netsuite.netsuite.client import NetSuiteClient
        from sanmar_netsuite.netsuite.repository import _sql_escape

        client = NetSuiteClient(ns_cfg().netsuite)
        probe = sorted(gtins | upcs)
        hits = 0
        for i in range(0, len(probe), 300):
            chunk = probe[i : i + 300]
            in_list = ", ".join(f"'{_sql_escape(v)}'" for v in chunk)
            rows = client.suiteql(
                f"SELECT upccode FROM item WHERE upccode IN ({in_list})"
            )
            hits += len({str(r["upccode"]) for r in rows})
        pct = 100.0 * hits / len(probe) if probe else 0.0
        print(
            f"\nbarcodes already on an existing NetSuite item: "
            f"{hits:,}/{len(probe):,} ({pct:.1f}%)"
        )

        # Style -> Vendor Name/Code coverage (the SanMar-style adopt lever).
        # Use the Item_SKU prefix as the canonical style token.
        style_tokens = sorted(
            {k.item_sku.split(".")[0] for s in styles for k in s.skus if k.item_sku}
        )
        shits = 0
        for i in range(0, len(style_tokens), 300):
            chunk = style_tokens[i : i + 300]
            in_list = ", ".join(f"'{_sql_escape(v)}'" for v in chunk)
            rows = client.suiteql(
                f"SELECT DISTINCT vendorname FROM item WHERE vendorname IN ({in_list})"
            )
            shits += len(rows)
        spct = 100.0 * shits / len(style_tokens) if style_tokens else 0.0
        print(
            f"styles present as a Vendor Name/Code on existing items: "
            f"{shits:,}/{len(style_tokens):,} ({spct:.1f}%)"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"\n(NetSuite coverage skipped: {exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

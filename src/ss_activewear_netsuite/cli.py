"""Command-line entry point for the S&S Activewear → NetSuite integration.

Subcommands:

* ``test-auth`` — verify S&S credentials by hitting ``/Styles`` once.
* ``download`` — write the full catalog snapshot to ``downloads/ss/products.json``.
* ``sync-catalog`` — upsert each SKU into NetSuite as a matrix-child item.
* ``sync-pricing`` — push pricing (sale + price levels + custom fields).
* ``sync-inventory`` — push availability totals + per-warehouse breakdown.
* ``export-csv`` — emit the matrix-item Import Assistant CSV.
* ``all`` — download + catalog + pricing + inventory in one pass.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Iterable
from pathlib import Path

from sanmar_netsuite.logging_config import configure_logging

from .config import SsAppConfig, get_config
from .models import SsProduct
from .ss_activewear.client import SsApiError, SsClient, product_from_payload
from .sync.catalog_sync import sync_products
from .sync.inventory_sync import sync_inventory
from .sync.pricing_sync import sync_pricing
from .transform.csv_export import write_csv

log = logging.getLogger(__name__)


def _client(config: SsAppConfig) -> SsClient:
    return SsClient(config.ss_api)


def _iter_products_from_file(path: Path) -> Iterable[SsProduct]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    rows = data if isinstance(data, list) else data.get("items") or []
    for row in rows:
        yield product_from_payload(row)


def _iter_products(config: SsAppConfig, file: str | None) -> Iterable[SsProduct]:
    """Load products either from a saved JSON file (default: downloads/ss/products.json)
    or live from the S&S API."""
    if file:
        return _iter_products_from_file(Path(file))
    default_file = Path(config.download_dir) / "products.json"
    if default_file.exists():
        log.info("Reading products from %s", default_file)
        return _iter_products_from_file(default_file)
    log.info("No local snapshot — streaming live from S&S API")
    return _client(config).iter_products()


# ── subcommands ──────────────────────────────────────────────────────────────
def cmd_test_auth(args: argparse.Namespace, config: SsAppConfig) -> int:
    client = _client(config)
    try:
        first = next(client.iter_styles(), None)
    except SsApiError as exc:
        log.error("S&S auth failed: %s", exc)
        return 1
    if first is None:
        log.warning("Auth OK but no styles returned (account may be empty?)")
        return 0
    log.info(
        "S&S auth OK — first style: id=%s name=%s brand=%s",
        first.style_id,
        first.style_name,
        first.brand_name,
    )
    return 0


def cmd_download(args: argparse.Namespace, config: SsAppConfig) -> int:
    out = Path(args.out or (Path(config.download_dir) / "products.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    client = _client(config)
    serialised: list[dict[str, object]] = []
    for product in client.iter_products():
        row = product.__dict__
        serialised.append(
            {
                **{k: v for k, v in row.items() if k != "warehouses"},
                "weight": _str_or_none(row.get("weight")),
                "piece_price": _str_or_none(row.get("piece_price")),
                "dozen_price": _str_or_none(row.get("dozen_price")),
                "case_price": _str_or_none(row.get("case_price")),
                "sale_price": _str_or_none(row.get("sale_price")),
                "customer_price": _str_or_none(row.get("customer_price")),
                "map_price": _str_or_none(row.get("map_price")),
                "msrp": _str_or_none(row.get("msrp")),
                "warehouses": [
                    {"warehouseAbbr": w.warehouse_abbr, "qty": w.qty}
                    for w in (row.get("warehouses") or ())
                ],
            }
        )
    out.write_text(json.dumps(serialised, indent=2), encoding="utf-8")
    log.info("Wrote %d products → %s", len(serialised), out)
    return 0


def _str_or_none(value: object) -> str | None:
    return str(value) if value is not None else None


def cmd_sync_catalog(args: argparse.Namespace, config: SsAppConfig) -> int:
    products = _iter_products(config, args.file)
    result = sync_products(products, config)
    return 0 if result.failed == 0 else 1


def cmd_sync_pricing(args: argparse.Namespace, config: SsAppConfig) -> int:
    products = _iter_products(config, args.file)
    result = sync_pricing(products, config)
    return 0 if result.failed == 0 else 1


def cmd_sync_inventory(args: argparse.Namespace, config: SsAppConfig) -> int:
    products = _iter_products(config, args.file)
    result = sync_inventory(products, config)
    return 0 if result.failed == 0 else 1


def cmd_export_csv(args: argparse.Namespace, config: SsAppConfig) -> int:
    products = _iter_products(config, args.file)
    out = Path(args.out or "./data/ss_matrix_items.csv")
    count = write_csv(products, out)
    log.info("Wrote %d rows → %s", count, out)
    return 0


def cmd_all(args: argparse.Namespace, config: SsAppConfig) -> int:
    rc = cmd_download(args, config)
    if rc:
        return rc
    rc = cmd_sync_catalog(args, config)
    if rc:
        return rc
    rc = cmd_sync_pricing(args, config)
    if rc:
        return rc
    return cmd_sync_inventory(args, config)


# ── argparse ────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ss-sync",
        description="Sync S&S Activewear product data into NetSuite.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("test-auth", help="Verify S&S API credentials")

    p_dl = sub.add_parser("download", help="Snapshot the full S&S catalog to JSON")
    p_dl.add_argument("--out", help="Output path", default=None)

    p_cat = sub.add_parser("sync-catalog", help="Upsert SKUs as matrix items")
    p_cat.add_argument("--file", help="Path to a saved products JSON snapshot", default=None)

    p_price = sub.add_parser("sync-pricing", help="Push pricing to NetSuite")
    p_price.add_argument("--file", help="Path to a saved products JSON snapshot", default=None)

    p_inv = sub.add_parser("sync-inventory", help="Push availability to NetSuite")
    p_inv.add_argument("--file", help="Path to a saved products JSON snapshot", default=None)

    p_csv = sub.add_parser("export-csv", help="Generate the matrix-item import CSV")
    p_csv.add_argument("--file", help="Path to a saved products JSON snapshot", default=None)
    p_csv.add_argument("--out", help="Output CSV path", default=None)

    p_all = sub.add_parser("all", help="Download + catalog + pricing + inventory")
    p_all.add_argument("--file", help="(used by sync sub-steps)", default=None)
    p_all.add_argument("--out", help="(used by download sub-step)", default=None)

    return parser


COMMANDS = {
    "test-auth": cmd_test_auth,
    "download": cmd_download,
    "sync-catalog": cmd_sync_catalog,
    "sync-pricing": cmd_sync_pricing,
    "sync-inventory": cmd_sync_inventory,
    "export-csv": cmd_export_csv,
    "all": cmd_all,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = get_config()
    configure_logging(config.sync.log_level)
    write_cmds = {"sync-catalog", "sync-pricing", "sync-inventory", "all"}
    if config.sync.dry_run and args.cmd in write_cmds:
        log.info("DRY RUN mode (set SYNC_DRY_RUN=false to write to NetSuite)")
    return COMMANDS[args.cmd](args, config)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

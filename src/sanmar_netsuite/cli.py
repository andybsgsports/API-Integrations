"""Command-line entrypoint for the SanMar → NetSuite integration.

Examples
--------
    # Download today's product files from SanMar SFTP
    sanmar-sync download

    # Dry-run the catalog sync against a downloaded file (no NetSuite calls)
    sanmar-sync sync-catalog --file downloads/SanMar_SDL_N.csv

    # Generate the one-time matrix-item CSV for NetSuite's Import Assistant
    sanmar-sync export-csv --file downloads/SanMar_SDL_N.csv --out data/matrix_items.csv

    # Full daily run (downloads, then catalog + pricing + inventory)
    sanmar-sync all

Set ``SYNC_DRY_RUN=false`` in the environment to actually write to NetSuite.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import AppConfig, get_config
from .logging_config import configure_logging
from .sanmar import constants as C
from .sanmar.sftp_client import SanMarSftp

log = logging.getLogger("sanmar_netsuite.cli")


# ── lazy helpers so dry-run / parse-only paths don't need NetSuite creds ──────
def _build_repo(config: AppConfig):
    from .netsuite.client import NetSuiteClient
    from .netsuite.repository import ItemRepository

    client = NetSuiteClient(config.netsuite)
    return ItemRepository(client, config.netsuite), client


def _resolve_file(file_arg: str | None, default_name: str, config: AppConfig) -> Path:
    """Return a local path for a SanMar file, downloading it if not provided."""
    if file_arg:
        return Path(file_arg)
    local = Path(config.sftp.download_dir) / default_name
    if local.exists():
        log.info("Using already-downloaded %s", local)
        return local
    log.info("%s not found locally; downloading from SanMar SFTP", default_name)
    return SanMarSftp(config.sftp).download(default_name)


# ── subcommand handlers ───────────────────────────────────────────────────────
def cmd_download(args: argparse.Namespace, config: AppConfig) -> int:
    sftp = SanMarSftp(config.sftp)
    files = args.files or [C.FILE_SDL_N, C.FILE_EPDD, C.FILE_DIP]
    downloaded = sftp.download_many(files)
    for name, path in downloaded.items():
        print(f"{name} -> {path}")
    return 0


def cmd_list_remote(args: argparse.Namespace, config: AppConfig) -> int:
    for name in sorted(SanMarSftp(config.sftp).list_dir(args.dir)):
        print(name)
    return 0


def cmd_sync_catalog(args: argparse.Namespace, config: AppConfig) -> int:
    from .sync.catalog_sync import sync_catalog

    path = _resolve_file(args.file, C.FILE_SDL_N, config)
    repo = None if config.sync.dry_run else _build_repo(config)[0]
    result = sync_catalog(path, config, repo=repo)
    print(result.summary())
    return 1 if result.failed else 0


def cmd_sync_pricing(args: argparse.Namespace, config: AppConfig) -> int:
    from .sync.pricing_sync import sync_pricing_from_catalog

    path = _resolve_file(args.file, C.FILE_SDL_N, config)
    repo = None if config.sync.dry_run else _build_repo(config)[0]
    result = sync_pricing_from_catalog(path, config, repo=repo)
    print(result.summary())
    return 1 if result.failed else 0


def cmd_sync_live_pricing(args: argparse.Namespace, config: AppConfig) -> int:
    from .sync.pricing_sync import sync_live_pricing

    path = _resolve_file(args.file, C.FILE_DIP, config)
    repo = None if config.sync.dry_run else _build_repo(config)[0]
    result = sync_live_pricing(path, config, repo=repo)
    print(result.summary())
    return 1 if result.failed else 0


def cmd_sync_inventory(args: argparse.Namespace, config: AppConfig) -> int:
    from .sync.inventory_sync import sync_inventory

    path = _resolve_file(args.file, C.FILE_DIP, config)
    repo = None if config.sync.dry_run else _build_repo(config)[0]
    result = sync_inventory(path, config, repo=repo)
    print(result.summary())
    return 1 if result.failed else 0


def cmd_sync_images(args: argparse.Namespace, config: AppConfig) -> int:
    from .netsuite.files import ImageUploader
    from .sync.image_sync import sync_images

    path = _resolve_file(args.file, C.FILE_SDL_N, config)
    repo = uploader = None
    if not config.sync.dry_run:
        repo, client = _build_repo(config)
        uploader = ImageUploader(client, args.folder)
    result = sync_images(path, config, args.folder, repo=repo, uploader=uploader)
    print(result.summary())
    return 1 if result.failed else 0


def cmd_export_csv(args: argparse.Namespace, config: AppConfig) -> int:
    from .sanmar.parsers import parse_styles
    from .transform.csv_export import write_matrix_csv

    path = _resolve_file(args.file, C.FILE_SDL_N, config)
    styles = parse_styles(path)
    parent_refs: dict[str, str] = {}
    skip: set[str] = set()
    if getattr(args, "merge", False):
        from .netsuite.client import NetSuiteClient
        from .netsuite.merge import prepare_merge

        client = NetSuiteClient(config.netsuite)
        parent_refs, skip = prepare_merge(client, styles)
        log.info(
            "Merge mode: %d parent id(s) resolved, %d existing combo(s) skipped",
            len(parent_refs),
            len(skip),
        )
    out = write_matrix_csv(
        styles,
        args.out,
        tax_schedule=config.sync.tax_schedule,
        income_account=config.sync.income_account,
        parent_refs=parent_refs,
        skip_external_ids=skip,
    )
    total = sum(len(s.skus) for s in styles)
    print(f"Wrote {total - len(skip)} of {total} SKU rows ({len(skip)} skipped) -> {out}")
    return 0


def _resolve_account_id(client, account_ref: str) -> str | None:
    """Resolve an account reference ('4100' or '4100 NAME') to its internal id."""
    from .netsuite.repository import _sql_escape

    number = account_ref.split()[0] if account_ref else account_ref
    rows = client.suiteql(f"SELECT id FROM account WHERE acctnumber = '{_sql_escape(number)}'")
    return str(rows[0]["id"]) if rows else None


def cmd_reconcile_items(args: argparse.Namespace, config: AppConfig) -> int:
    """Set income account + Base Price on SanMar matrix children (post-import).

    NetSuite's CSV matrix import can't apply these to children, so this reconciles
    them by external id over REST. Writes only when ``SYNC_DRY_RUN=false``.
    """
    from .netsuite.client import NetSuiteClient
    from .netsuite.reconcile import reconcile_items
    from .netsuite.repository import ItemRepository
    from .sanmar.parsers import parse_styles

    path = _resolve_file(args.file, C.FILE_SDL_N, config)
    styles = parse_styles(path)
    client = NetSuiteClient(config.netsuite)
    income_id = _resolve_account_id(client, config.sync.income_account)
    if income_id is None:
        log.error("Income account %r not found in NetSuite.", config.sync.income_account)
        return 2
    repo = ItemRepository(client, config.netsuite)
    report = reconcile_items(
        repo, styles, income_account_id=income_id, allow_write=not config.sync.dry_run
    )
    print(report.summary())
    return 0


def cmd_push_children(args: argparse.Namespace, config: AppConfig) -> int:
    """Create matrix children via the BSG RESTlet (handles numeric-style parents).

    The RESTlet resolves the parent by name in SuiteScript, so numeric styles
    like ``2000`` link correctly — unlike the CSV importer. In dry-run it prints
    the JSON payload(s) without calling NetSuite; with ``SYNC_DRY_RUN=false`` it
    POSTs them and prints each child's created/updated/error result. Use
    ``--style 2000 --limit 1`` to fire a single child as a smoke test.
    """
    import json

    from .sanmar.parsers import parse_styles
    from .transform.csv_export import DEFAULT_ASSET_ACCOUNT, DEFAULT_COGS_ACCOUNT
    from .transform.restlet_payload import iter_child_payloads

    path = _resolve_file(args.file, C.FILE_SDL_N, config)
    styles = parse_styles(path)
    payloads = list(
        iter_child_payloads(
            styles,
            income_account=config.sync.income_account,
            cogs_account=DEFAULT_COGS_ACCOUNT,
            asset_account=DEFAULT_ASSET_ACCOUNT,
            tax_schedule=config.sync.tax_schedule,
            style_filter=args.style,
            limit=args.limit,
        )
    )
    if not payloads:
        log.error("No SKUs matched (style=%r). Nothing to push.", args.style)
        return 2

    if config.sync.dry_run:
        log.info("DRY RUN: would POST %d child item(s) to the matrix RESTlet:", len(payloads))
        print(json.dumps({"items": payloads}, indent=2))
        return 0

    from .netsuite.client import NetSuiteClient

    client = NetSuiteClient(config.netsuite)
    response = client.call_restlet(
        config.netsuite.matrix_script_id,
        config.netsuite.matrix_deploy_id,
        {"items": payloads},
    )
    results = response.get("results", [])
    errors = 0
    for r in results:
        status = r.get("status")
        if status == "error":
            errors += 1
        print(
            f"{r.get('externalId', '?'):<24} {status:<8} "
            f"{('id=' + str(r['id'])) if r.get('id') else r.get('message', '')}"
        )
    print(f"\n{len(results)} item(s): {len(results) - errors} ok, {errors} error(s).")
    return 1 if errors else 0


def cmd_ensure_matrix_options(args: argparse.Namespace, config: AppConfig) -> int:
    """Create (or, in dry-run, preview) any missing matrix color/size values.

    Reads the live matrix lists, so it needs NetSuite credentials even when
    previewing. Writes only when ``SYNC_DRY_RUN=false``.
    """
    from .netsuite.client import NetSuiteClient, NetSuiteError
    from .netsuite.matrix_options import ensure_matrix_options
    from .sanmar.parsers import parse_styles

    path = _resolve_file(args.file, C.FILE_SDL_N, config)
    styles = parse_styles(path)
    client = NetSuiteClient(config.netsuite)
    try:
        report = ensure_matrix_options(client, styles, allow_create=not config.sync.dry_run)
    except NetSuiteError as exc:
        if exc.status == 403:
            log.error(
                "NetSuite refused the write (403 INSUFFICIENT_PERMISSION). The "
                "integration role needs the 'Custom Lists' permission at Full level to "
                "add matrix color/size values. Ask a NetSuite admin to grant it "
                "(Setup > Users/Roles > Manage Roles > [integration role] > Permissions "
                "> Setup tab > add 'Custom Lists' = Full), then re-run this command."
            )
            return 2
        raise
    print(report.summary())
    return 0


def cmd_all(args: argparse.Namespace, config: AppConfig) -> int:
    from .sync.catalog_sync import sync_catalog
    from .sync.inventory_sync import sync_inventory
    from .sync.pricing_sync import sync_pricing_from_catalog

    catalog_path = _resolve_file(args.catalog_file, C.FILE_SDL_N, config)
    dip_path = _resolve_file(args.dip_file, C.FILE_DIP, config)
    repo = None if config.sync.dry_run else _build_repo(config)[0]

    overall_failed = 0
    for label, fn, p in (
        ("catalog", sync_catalog, catalog_path),
        ("pricing", sync_pricing_from_catalog, catalog_path),
        ("inventory", sync_inventory, dip_path),
    ):
        log.info("=== Running %s sync ===", label)
        result = fn(p, config, repo=repo)
        print(result.summary())
        overall_failed += result.failed
    return 1 if overall_failed else 0


# ── arg parsing ───────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sanmar-sync", description="Sync SanMar product data into NetSuite."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("download", help="Download SanMar product files via SFTP")
    p.add_argument("files", nargs="*", help="Specific filenames (default: SDL_N, EPDD, dip)")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("list-remote", help="List files in the SanMar SFTP folder")
    p.add_argument("--dir", default=None, help="Remote dir (default: configured)")
    p.set_defaults(func=cmd_list_remote)

    p = sub.add_parser("sync-catalog", help="Upsert styles/SKUs as matrix items")
    p.add_argument("--file", default=None, help="Path to SDL_N/EPDD CSV")
    p.set_defaults(func=cmd_sync_catalog)

    p = sub.add_parser("sync-pricing", help="Upsert regular pricing from catalog feed")
    p.add_argument("--file", default=None, help="Path to SDL_N/EPDD CSV")
    p.set_defaults(func=cmd_sync_pricing)

    p = sub.add_parser("sync-live-pricing", help="Upsert sale-aware pricing from dip feed")
    p.add_argument("--file", default=None, help="Path to sanmar_dip.txt")
    p.set_defaults(func=cmd_sync_live_pricing)

    p = sub.add_parser("sync-inventory", help="Upsert availability from dip feed")
    p.add_argument("--file", default=None, help="Path to sanmar_dip.txt")
    p.set_defaults(func=cmd_sync_inventory)

    p = sub.add_parser("sync-images", help="Upload images to the File Cabinet")
    p.add_argument("--file", default=None, help="Path to SDL_N/EPDD CSV")
    p.add_argument("--folder", required=True, help="File Cabinet folder internal id")
    p.set_defaults(func=cmd_sync_images)

    p = sub.add_parser("export-csv", help="Generate the matrix-item import CSV")
    p.add_argument("--file", default=None, help="Path to SDL_N/EPDD CSV")
    p.add_argument("--out", default="data/matrix_items.csv", help="Output CSV path")
    p.add_argument(
        "--merge",
        action="store_true",
        help="Merge-aware export: reference parents by internal id and skip "
        "existing color/size combos (needs NetSuite creds)",
    )
    p.set_defaults(func=cmd_export_csv)

    p = sub.add_parser(
        "reconcile-items",
        help="Set income account + Base Price on imported matrix children (post-import)",
    )
    p.add_argument("--file", default=None, help="Path to SDL_N/EPDD CSV")
    p.set_defaults(func=cmd_reconcile_items)

    p = sub.add_parser(
        "push-children",
        help="Create matrix children via the RESTlet (works for numeric-style "
        "parents; preview unless SYNC_DRY_RUN=false)",
    )
    p.add_argument("--file", default=None, help="Path to SDL_N/EPDD CSV")
    p.add_argument("--style", default=None, help="Only push children of this style (e.g. 2000)")
    p.add_argument(
        "--limit", type=int, default=0, help="Cap the number of children pushed (0 = no cap)"
    )
    p.set_defaults(func=cmd_push_children)

    p = sub.add_parser(
        "ensure-matrix-options",
        help="Create any missing matrix color/size list values (preview unless "
        "SYNC_DRY_RUN=false)",
    )
    p.add_argument("--file", default=None, help="Path to SDL_N/EPDD CSV")
    p.set_defaults(func=cmd_ensure_matrix_options)

    p = sub.add_parser("all", help="Download + catalog + pricing + inventory")
    p.add_argument("--catalog-file", default=None, help="Path to SDL_N/EPDD CSV")
    p.add_argument("--dip-file", default=None, help="Path to sanmar_dip.txt")
    p.set_defaults(func=cmd_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = get_config()
    configure_logging(config.sync.log_level)
    if config.sync.dry_run:
        log.info("DRY RUN mode (set SYNC_DRY_RUN=false to write to NetSuite)")
    try:
        return args.func(args, config)
    except KeyboardInterrupt:  # pragma: no cover
        log.warning("Interrupted")
        return 130
    except Exception as exc:  # noqa: BLE001
        log.exception("Command failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())

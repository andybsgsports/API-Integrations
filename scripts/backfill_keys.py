"""SanMar → NetSuite key back-fill (runs on CI).

Re-runs the read-only reconciliation match, then writes onto every matched
existing item: the feed **UPC** (and optionally a ``SANMAR-<key>`` external id),
plus applies the color full-name renames. Honors ``SYNC_DRY_RUN`` — the default
run only *reports* what it would write.

Env knobs:
    RECONCILE_STYLE_LIMIT       0 = all styles (default), N for a sample
    BACKFILL_MAX_ITEMS          cap on item writes (0 = no cap)
    BACKFILL_SET_EXTERNAL_IDS   "true" to also re-key external ids (overwrites
                                the migrated numeric ids — deliberate opt-in)
"""

from __future__ import annotations

import os
from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.adopt import match_existing
from sanmar_netsuite.netsuite.backfill import backfill_keys
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_styles
from sanmar_netsuite.sanmar.sftp_client import SanMarSftp


def _flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    cfg = get_config()
    style_limit = int(os.environ.get("RECONCILE_STYLE_LIMIT", "0") or "0")
    max_items = int(os.environ.get("BACKFILL_MAX_ITEMS", "0") or "0")
    set_eids = _flag("BACKFILL_SET_EXTERNAL_IDS")
    allow_write = not cfg.sync.dry_run

    catalog = Path(cfg.sftp.download_dir) / C.FILE_SDL_N
    if not catalog.exists():
        catalog = SanMarSftp(cfg.sftp).download(C.FILE_SDL_N)
    styles = parse_styles(catalog)
    print(f"Parsed {len(styles)} styles from {catalog}")

    client = NetSuiteClient(cfg.netsuite)
    report = match_existing(client, styles, style_limit=style_limit)
    print("\n" + report.summary())

    print(
        f"\nBack-fill config: allow_write={allow_write} max_items={max_items} "
        f"set_external_ids={set_eids}"
    )
    result = backfill_keys(
        client,
        report,
        allow_write=allow_write,
        max_items=max_items,
        set_external_ids=set_eids,
    )
    print("\n" + result.summary())
    return 1 if result.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

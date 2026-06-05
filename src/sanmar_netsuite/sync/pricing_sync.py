"""Pricing sync.

Two modes:

* ``sync_pricing_from_catalog`` — regular piece/case/MSRP pricing from the
  SDL_N/EPDD catalog feed. Run daily.
* ``sync_live_pricing`` — sale-aware base pricing from the hourly
  ``sanmar_dip.txt`` feed. Run as often as the sale data matters.

Both resolve the NetSuite item by external id (preferring the internal id cached
by the catalog sync) and PATCH only the price sublist + price custom fields.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import AppConfig
from ..netsuite.repository import ItemRepository, child_external_id
from ..sanmar.parsers import parse_inventory, parse_styles
from ..state.cache import StateCache, hash_payload
from ..transform.pricing import build_live_price_body, build_price_body
from .base import SyncResult

log = logging.getLogger(__name__)


def sync_pricing_from_catalog(
    file_path: str | Path,
    config: AppConfig,
    *,
    repo: ItemRepository | None = None,
    cache: StateCache | None = None,
) -> SyncResult:
    result = SyncResult(entity="pricing")
    styles = parse_styles(file_path)
    owns_cache = cache is None
    cache = cache or StateCache(config.sync.state_db_path)
    dry_run = config.sync.dry_run
    if not dry_run and repo is None:
        raise ValueError("repo is required when not in dry-run mode")

    try:
        for style in styles:
            for sku in style.skus:
                if config.sync.max_records and result.processed >= config.sync.max_records:
                    break
                body = build_price_body(sku, config.netsuite)
                _apply(result, cache, repo, sku.unique_key, body, dry_run)
    finally:
        if owns_cache:
            cache.close()

    log.info(result.summary())
    return result


def sync_live_pricing(
    file_path: str | Path,
    config: AppConfig,
    *,
    repo: ItemRepository | None = None,
    cache: StateCache | None = None,
) -> SyncResult:
    result = SyncResult(entity="pricing-live")
    records = parse_inventory(file_path)
    owns_cache = cache is None
    cache = cache or StateCache(config.sync.state_db_path)
    dry_run = config.sync.dry_run
    if not dry_run and repo is None:
        raise ValueError("repo is required when not in dry-run mode")

    try:
        for record in records:
            if config.sync.max_records and result.processed >= config.sync.max_records:
                break
            body = build_live_price_body(record, config.netsuite)
            if not body:
                continue
            _apply(result, cache, repo, record.unique_key, body, dry_run)
    finally:
        if owns_cache:
            cache.close()

    log.info(result.summary())
    return result


def _apply(
    result: SyncResult,
    cache: StateCache,
    repo: ItemRepository | None,
    unique_key: str,
    body: dict,
    dry_run: bool,
) -> None:
    eid = child_external_id(unique_key)
    payload_hash = hash_payload(body)
    result.processed += 1
    if cache.is_unchanged("pricing", eid, payload_hash):
        result.skipped_unchanged += 1
        return
    if dry_run:
        log.debug("[dry-run] pricing %s -> %s", eid, body)
        result.updated += 1
        return
    assert repo is not None  # guaranteed by the caller's non-dry-run check
    try:
        internal_id = cache.get_netsuite_id("sku", eid) or repo.find_id_by_external_id(eid)
        if internal_id is None:
            internal_id = repo.upsert(eid, body)
        else:
            repo.update_fields(internal_id, body)
        cache.upsert("pricing", eid, payload_hash, internal_id)
        result.updated += 1
    except Exception as exc:  # noqa: BLE001
        result.record_failure(eid, str(exc))

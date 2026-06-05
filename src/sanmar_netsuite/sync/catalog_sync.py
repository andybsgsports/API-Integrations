"""Catalog sync: SanMar styles/SKUs -> NetSuite matrix items.

Reads ``SanMar_SDL_N.csv`` (or EPDD), transforms each style + SKU into NetSuite
item bodies, and upserts them by external id — skipping records whose payload is
byte-identical to the last successful push (delta cache).

In dry-run mode no NetSuite calls are made; payloads are logged and the delta
cache is left untouched.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import AppConfig
from ..netsuite.repository import (
    ItemRepository,
    child_external_id,
    parent_external_id,
)
from ..sanmar.parsers import parse_styles
from ..state.cache import StateCache, hash_payload
from ..transform.catalog import build_child_payload, build_parent_payload
from .base import SyncResult

log = logging.getLogger(__name__)


def sync_catalog(
    file_path: str | Path,
    config: AppConfig,
    *,
    repo: ItemRepository | None = None,
    cache: StateCache | None = None,
) -> SyncResult:
    result = SyncResult(entity="catalog")
    styles = parse_styles(file_path)
    log.info("Parsed %d styles from %s", len(styles), file_path)

    owns_cache = cache is None
    cache = cache or StateCache(config.sync.state_db_path)
    dry_run = config.sync.dry_run
    if not dry_run and repo is None:
        raise ValueError("repo is required when not in dry-run mode")

    max_records = config.sync.max_records
    try:
        for style in styles:
            if max_records and result.processed >= max_records:
                log.info("Reached SYNC_MAX_RECORDS=%d; stopping.", max_records)
                break
            _sync_one_style(style, config, repo, cache, result, dry_run)
    finally:
        if owns_cache:
            cache.close()

    log.info(result.summary())
    return result


def _sync_one_style(style, config, repo, cache, result: SyncResult, dry_run: bool) -> None:
    ns = config.netsuite
    # ── parent ──
    parent_eid = parent_external_id(style.style)
    parent_body = build_parent_payload(style, ns)
    parent_hash = hash_payload(parent_body)
    result.processed += 1
    if cache.is_unchanged("style", parent_eid, parent_hash):
        result.skipped_unchanged += 1
    else:
        if dry_run:
            log.debug("[dry-run] parent %s -> %s", parent_eid, parent_body)
            result.updated += 1
        else:
            try:
                internal_id = repo.upsert(parent_eid, parent_body)
                cache.upsert("style", parent_eid, parent_hash, internal_id)
                result.updated += 1
            except Exception as exc:  # noqa: BLE001 - tally and continue
                result.record_failure(parent_eid, str(exc))
                return  # don't push children if the parent failed

    # ── children ──
    for sku in style.skus:
        child_eid = child_external_id(sku.unique_key)
        child_body = build_child_payload(sku, style, ns)
        child_hash = hash_payload(child_body)
        result.processed += 1
        if cache.is_unchanged("sku", child_eid, child_hash):
            result.skipped_unchanged += 1
            continue
        if dry_run:
            log.debug("[dry-run] child %s -> %s", child_eid, child_body)
            result.updated += 1
            continue
        try:
            internal_id = repo.upsert(child_eid, child_body)
            cache.upsert("sku", child_eid, child_hash, internal_id)
            result.updated += 1
        except Exception as exc:  # noqa: BLE001
            result.record_failure(child_eid, str(exc))

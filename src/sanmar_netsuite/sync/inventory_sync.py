"""Inventory availability sync from ``sanmar_dip.txt``.

Writes SanMar's per-warehouse and total availability onto custom item fields
(see :mod:`sanmar_netsuite.transform.inventory` for why this does NOT touch
NetSuite's real on-hand quantity).
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import AppConfig
from ..netsuite.repository import ItemRepository, child_external_id
from ..sanmar.parsers import parse_inventory
from ..state.cache import StateCache, hash_payload
from ..transform.inventory import availability_summary, build_availability_body
from .base import SyncResult

log = logging.getLogger(__name__)


def sync_inventory(
    file_path: str | Path,
    config: AppConfig,
    *,
    repo: ItemRepository | None = None,
    cache: StateCache | None = None,
) -> SyncResult:
    result = SyncResult(entity="inventory")
    records = parse_inventory(file_path)
    log.info("Parsed %d SKUs from %s", len(records), file_path)

    owns_cache = cache is None
    cache = cache or StateCache(config.sync.state_db_path)
    dry_run = config.sync.dry_run
    if not dry_run and repo is None:
        raise ValueError("repo is required when not in dry-run mode")

    try:
        for record in records:
            if config.sync.max_records and result.processed >= config.sync.max_records:
                break
            body = build_availability_body(record, config.netsuite)
            eid = child_external_id(record.unique_key)
            payload_hash = hash_payload(body)
            result.processed += 1
            if cache.is_unchanged("inventory", eid, payload_hash):
                result.skipped_unchanged += 1
                continue
            if dry_run:
                log.debug("[dry-run] inventory %s", availability_summary(record))
                result.updated += 1
                continue
            assert repo is not None  # guaranteed by the non-dry-run check above
            try:
                internal_id = cache.get_netsuite_id("sku", eid) or repo.find_id_by_external_id(
                    eid
                )
                if internal_id is None:
                    log.warning("No NetSuite item for %s; run catalog sync first.", eid)
                    result.skipped_unchanged += 1
                    continue
                repo.update_fields(internal_id, body)
                cache.upsert("inventory", eid, payload_hash, internal_id)
                result.updated += 1
            except Exception as exc:  # noqa: BLE001
                result.record_failure(eid, str(exc))
    finally:
        if owns_cache:
            cache.close()

    log.info(result.summary())
    return result

"""Sync S&S pricing to NetSuite price levels + custom fields."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sanmar_netsuite.netsuite.client import NetSuiteClient, NetSuiteError
from sanmar_netsuite.sync.base import SyncResult

from ..config import SsAppConfig
from ..models import SsProduct
from ..transform.catalog import sku_external_id
from ..transform.pricing import build_pricing_payload

log = logging.getLogger(__name__)


def sync_pricing(
    products: Iterable[SsProduct],
    config: SsAppConfig,
    *,
    dry_run: bool | None = None,
    max_records: int | None = None,
) -> SyncResult:
    if dry_run is None:
        dry_run = config.sync.dry_run
    if max_records is None:
        max_records = config.sync.max_records or None

    result = SyncResult(entity="ss-pricing")
    client = None if dry_run else NetSuiteClient(config.netsuite)

    for product in products:
        if not product.sku:
            continue
        if max_records is not None and result.processed >= max_records:
            break
        result.processed += 1
        ext_id = sku_external_id(product)
        body = build_pricing_payload(product, config)
        if not body:
            result.skipped_unchanged += 1
            continue
        try:
            if dry_run or client is None:
                log.info("[dry-run] pricing %s → %s", ext_id, body)
                result.updated += 1
            else:
                client.upsert_by_external_id("inventoryItem", ext_id, body)
                result.updated += 1
        except NetSuiteError as exc:
            result.record_failure(ext_id, str(exc))
    log.info(result.summary())
    return result

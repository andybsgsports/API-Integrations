"""Sync S&S availability counts to NetSuite custom fields."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sanmar_netsuite.netsuite.client import NetSuiteClient, NetSuiteError
from sanmar_netsuite.sync.base import SyncResult

from ..config import SsAppConfig
from ..models import SsProduct
from ..transform.catalog import sku_external_id
from ..transform.inventory import build_inventory_payload

log = logging.getLogger(__name__)


def sync_inventory(
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

    result = SyncResult(entity="ss-inventory")
    client = None if dry_run else NetSuiteClient(config.netsuite)

    for product in products:
        if not product.sku:
            continue
        if max_records is not None and result.processed >= max_records:
            break
        result.processed += 1
        ext_id = sku_external_id(product)
        body = build_inventory_payload(product, config)
        try:
            if dry_run or client is None:
                log.info("[dry-run] inventory %s → %s", ext_id, body)
                result.updated += 1
            else:
                client.upsert_by_external_id("inventoryItem", ext_id, body)
                result.updated += 1
        except NetSuiteError as exc:
            result.record_failure(ext_id, str(exc))
    log.info(result.summary())
    return result

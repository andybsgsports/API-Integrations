"""Pull S&S products and upsert each as a NetSuite matrix-child inventory item."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sanmar_netsuite.netsuite.client import NetSuiteClient, NetSuiteError
from sanmar_netsuite.sync.base import SyncResult

from ..config import SsAppConfig
from ..models import SsProduct
from ..transform.catalog import build_item_payload, sku_external_id

log = logging.getLogger(__name__)


def sync_products(
    products: Iterable[SsProduct],
    config: SsAppConfig,
    *,
    dry_run: bool | None = None,
    max_records: int | None = None,
) -> SyncResult:
    """Push each S&S product through NetSuite item upsert by external id.

    ``dry_run`` defaults to the value in :class:`SsAppConfig.sync`. When True,
    payloads are logged but no NetSuite calls are made.
    """

    if dry_run is None:
        dry_run = config.sync.dry_run
    if max_records is None:
        max_records = config.sync.max_records or None

    result = SyncResult(entity="ss-catalog")
    client = None if dry_run else NetSuiteClient(config.netsuite)

    for product in products:
        if not product.sku:
            log.debug("skipping product with empty sku: %r", product)
            continue
        if max_records is not None and result.processed >= max_records:
            break
        result.processed += 1
        ext_id = sku_external_id(product)
        body = build_item_payload(product, config)
        try:
            if dry_run or client is None:
                log.info("[dry-run] upsert %s → %s", ext_id, _trim_for_log(body))
                result.updated += 1
            else:
                client.upsert_by_external_id("inventoryItem", ext_id, body)
                result.updated += 1
        except NetSuiteError as exc:
            result.record_failure(ext_id, str(exc))
    log.info(result.summary())
    return result


def _trim_for_log(body: dict[str, object]) -> dict[str, object]:
    # Keep log lines readable — drop long fields when they're empty-ish.
    return {k: v for k, v in body.items() if v not in (None, "", [], {})}

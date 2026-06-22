"""End-to-end dry-run tests: parse -> transform -> delta cache, no NetSuite."""

from __future__ import annotations

from sanmar_netsuite.config import AppConfig, NetSuiteFieldMap, SftpConfig, SyncConfig
from sanmar_netsuite.state.cache import StateCache, hash_payload
from sanmar_netsuite.sync.catalog_sync import sync_catalog
from sanmar_netsuite.sync.inventory_sync import sync_inventory


def _app_config(tmp_path) -> AppConfig:
    from sanmar_netsuite.config import NetSuiteConfig

    return AppConfig(
        sftp=SftpConfig(
            host="ftp.sanmar.com",
            port=2200,
            username="298728",
            password="",
            remote_dir="/SanMarPDD",
            host_key="",
            download_dir=str(tmp_path / "downloads"),
        ),
        netsuite=NetSuiteConfig(
            account_id="1234567",
            consumer_key="",
            consumer_secret="",
            token_id="",
            token_secret="",
            rest_base="https://1234567.suitetalk.api.netsuite.com",
            sanmar_vendor_id="42",
            subsidiary_id="1",
            income_account_id="101",
            asset_account_id="102",
            cogs_account_id="103",
            price_level_base="1",
            price_level_case="",
            price_level_msrp="",
            fields=NetSuiteFieldMap.from_env(),
        ),
        sync=SyncConfig(
            state_db_path=str(tmp_path / "state.db"),
            max_records=0,
            dry_run=True,
            log_level="INFO",
            tax_schedule="Taxable",
            income_account="SALES OF MERCHANDISE",
        ),
    )


def test_catalog_dry_run_counts(tmp_path, sdl_n_path):
    config = _app_config(tmp_path)
    result = sync_catalog(sdl_n_path, config)
    # 2 styles (parents) + 5 SKUs (children) = 7 processed records
    assert result.processed == 7
    assert result.updated == 7
    assert result.failed == 0
    assert result.skipped_unchanged == 0


def test_inventory_dry_run(tmp_path, dip_path):
    config = _app_config(tmp_path)
    result = sync_inventory(dip_path, config)
    # 4 distinct unique keys in the dip fixture
    assert result.processed == 4
    assert result.failed == 0


def test_delta_cache_skips_unchanged(tmp_path):
    cache = StateCache(tmp_path / "delta.db")
    payload = {"itemId": "K420", "price": 9.99}
    h = hash_payload(payload)
    assert cache.is_unchanged("style", "SANMAR-K420", h) is False
    cache.upsert("style", "SANMAR-K420", h, "5001")
    assert cache.is_unchanged("style", "SANMAR-K420", h) is True
    assert cache.get_netsuite_id("style", "SANMAR-K420") == "5001"
    # Changed payload -> not unchanged
    h2 = hash_payload({"itemId": "K420", "price": 10.49})
    assert cache.is_unchanged("style", "SANMAR-K420", h2) is False
    cache.close()


def test_max_records_limit(tmp_path, sdl_n_path):
    config = _app_config(tmp_path)
    config = config.__class__(
        sftp=config.sftp,
        netsuite=config.netsuite,
        sync=SyncConfig(
            state_db_path=str(tmp_path / "state2.db"),
            max_records=1,
            dry_run=True,
            log_level="INFO",
            tax_schedule="Taxable",
            income_account="SALES OF MERCHANDISE",
        ),
    )
    result = sync_catalog(sdl_n_path, config)
    assert result.processed >= 1

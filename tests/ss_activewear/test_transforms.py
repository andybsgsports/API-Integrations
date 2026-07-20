"""Tests for the S&S → NetSuite transforms.

Mocks the env so we don't depend on a real ``.env`` during CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ss_activewear_netsuite.config import SsAppConfig
from ss_activewear_netsuite.ss_activewear.client import product_from_payload
from ss_activewear_netsuite.transform.catalog import (
    build_item_payload,
    sku_external_id,
    style_external_id,
    warehouses_to_payload,
)
from ss_activewear_netsuite.transform.csv_export import write_csv
from ss_activewear_netsuite.transform.inventory import build_inventory_payload
from ss_activewear_netsuite.transform.pricing import build_pricing_payload


@pytest.fixture
def fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SS_API_ACCOUNT_NUMBER", "07548")
    monkeypatch.setenv("SS_API_KEY", "test-key")
    monkeypatch.setenv("NETSUITE_ACCOUNT_ID", "1234567_SB1")
    monkeypatch.setenv("NETSUITE_CONSUMER_KEY", "ck")
    monkeypatch.setenv("NETSUITE_CONSUMER_SECRET", "cs")
    monkeypatch.setenv("NETSUITE_TOKEN_ID", "tid")
    monkeypatch.setenv("NETSUITE_TOKEN_SECRET", "tsec")
    monkeypatch.setenv("NETSUITE_INCOME_ACCOUNT_ID", "111")
    monkeypatch.setenv("NETSUITE_ASSET_ACCOUNT_ID", "222")
    monkeypatch.setenv("NETSUITE_COGS_ACCOUNT_ID", "333")
    monkeypatch.setenv("NETSUITE_SUBSIDIARY_ID", "1")
    monkeypatch.setenv("NETSUITE_SS_VENDOR_ID", "999")
    monkeypatch.setenv("NETSUITE_PRICE_LEVEL_BASE", "1")
    monkeypatch.setenv("NETSUITE_PRICE_LEVEL_CASE", "5")
    monkeypatch.setenv("NETSUITE_PRICE_LEVEL_MSRP", "7")
    monkeypatch.setenv("SYNC_DRY_RUN", "true")
    # Defeat lru_cache on get_config so each test gets a fresh load
    import ss_activewear_netsuite.config as cfg_mod
    cfg_mod.get_config.cache_clear()


@pytest.fixture
def cfg(fake_env: None) -> SsAppConfig:
    return SsAppConfig.from_env()


def test_external_ids_are_prefixed(ss_products_path: Path) -> None:
    rows = json.loads(ss_products_path.read_text())
    product = product_from_payload(rows[0])
    assert sku_external_id(product) == "SS-B00120001"
    assert style_external_id(product) == "SS-STYLE-100"


def test_warehouses_serialise_compactly(ss_products_path: Path) -> None:
    rows = json.loads(ss_products_path.read_text())
    product = product_from_payload(rows[0])
    assert warehouses_to_payload(product) == "IL:600;TX:400;NV:234"


def test_warehouses_empty_when_no_stock(ss_products_path: Path) -> None:
    rows = json.loads(ss_products_path.read_text())
    product = product_from_payload(rows[3])
    assert warehouses_to_payload(product) == ""


def test_build_item_payload_carries_all_custom_fields(
    cfg: SsAppConfig, ss_products_path: Path
) -> None:
    rows = json.loads(ss_products_path.read_text())
    pa55 = product_from_payload(rows[2])
    body = build_item_payload(pa55, cfg)

    assert body["itemId"] == "PA55:Classic Navy:L"
    assert body[cfg.netsuite_fields.sku] == "B00187730"
    assert body[cfg.netsuite_fields.style_id] == "200"
    assert body[cfg.netsuite_fields.brand] == "Port Authority"
    assert body[cfg.netsuite_fields.gtin] == "00821780000118"
    assert body[cfg.netsuite_fields.map_price] == 15.99
    assert body[cfg.netsuite_fields.msrp] == 22.0
    assert body[cfg.netsuite_fields.qty_available] == 412
    assert body["incomeAccount"] == {"id": "111"}
    assert body["assetAccount"] == {"id": "222"}
    assert body["cogsAccount"] == {"id": "333"}
    assert body["subsidiary"] == [{"id": "1"}]
    assert body["vendor"] == {"id": "999"}
    # base price is sale_price when present
    assert body["price"][0]["priceLevel"]["id"] == "1"
    assert body["price"][0]["price"][0]["value"] == 7.99


def test_discontinued_item_is_inactive(cfg: SsAppConfig, ss_products_path: Path) -> None:
    rows = json.loads(ss_products_path.read_text())
    dt6000 = product_from_payload(rows[3])
    body = build_item_payload(dt6000, cfg)
    assert body["isInactive"] is True
    assert body[cfg.netsuite_fields.is_discontinued] is True


def test_inventory_payload_only_carries_qty(
    cfg: SsAppConfig, ss_products_path: Path
) -> None:
    rows = json.loads(ss_products_path.read_text())
    g500 = product_from_payload(rows[0])
    body = build_inventory_payload(g500, cfg)
    assert set(body) == {cfg.netsuite_fields.qty_available}
    assert body[cfg.netsuite_fields.qty_available] == 1234


def test_pricing_payload_uses_optional_price_levels(
    cfg: SsAppConfig, ss_products_path: Path
) -> None:
    rows = json.loads(ss_products_path.read_text())
    pa55 = product_from_payload(rows[2])
    body = build_pricing_payload(pa55, cfg)
    # Base, case, MSRP all populated since their price levels are configured.
    price_levels = {entry["priceLevel"]["id"] for entry in body["price"]}
    assert price_levels == {"1", "5", "7"}


def test_csv_export(cfg: SsAppConfig, ss_products_path: Path, tmp_path: Path) -> None:
    rows = json.loads(ss_products_path.read_text())
    products = [product_from_payload(r) for r in rows]
    out = tmp_path / "ss_matrix_items.csv"
    count = write_csv(products, out)
    assert count == 4
    content = out.read_text()
    assert "External ID,Parent External ID" in content.splitlines()[0]
    assert "SS-B00120001" in content
    assert "SS-STYLE-100" in content
    assert "Port Authority" in content
    assert "Yes" in content  # at least one discontinued row

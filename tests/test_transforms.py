from __future__ import annotations

import json

from sanmar_netsuite.config import NetSuiteConfig, NetSuiteFieldMap
from sanmar_netsuite.sanmar.parsers import parse_inventory, parse_styles
from sanmar_netsuite.transform.catalog import (
    build_child_payload,
    build_parent_payload,
)
from sanmar_netsuite.transform.inventory import build_availability_body
from sanmar_netsuite.transform.pricing import (
    build_live_price_body,
    build_price_body,
)


def _config() -> NetSuiteConfig:
    return NetSuiteConfig(
        account_id="1234567",
        consumer_key="ck",
        consumer_secret="cs",
        token_id="ti",
        token_secret="ts",
        rest_base="https://1234567.suitetalk.api.netsuite.com",
        restlet_base="",
        matrix_script_id="",
        matrix_deploy_id="",
        sanmar_vendor_id="42",
        subsidiary_id="1",
        income_account_id="101",
        asset_account_id="102",
        cogs_account_id="103",
        price_level_base="1",
        price_level_case="5",
        price_level_msrp="6",
        fields=NetSuiteFieldMap.from_env(),
    )


def _k420(sdl_n_path):
    return next(s for s in parse_styles(sdl_n_path) if s.style == "K420")


def test_parent_payload_core_fields(sdl_n_path):
    style = _k420(sdl_n_path)
    body = build_parent_payload(style, _config())
    assert body["itemId"] == "K420"
    assert body["salesDescription"] == style.description
    assert body["vendor"] == {"id": "42"}
    assert body["subsidiary"] == {"items": [{"id": "1"}]}
    assert body["custitem_sanmar_style"] == "K420"
    # Primary image URL is carried on the parent.
    assert body["custitem_sanmar_front_image_url"].endswith("black_model_front.jpg")
    assert body["isInactive"] is False


def test_child_payload_identity_and_pricing_fields(sdl_n_path):
    style = _k420(sdl_n_path)
    sku = next(s for s in style.skus if s.color_name == "Black" and s.size == "S")
    body = build_child_payload(sku, style, _config())
    assert body["itemId"] == "K420:Black:S"
    assert body["custitem_sanmar_unique_key"] == "920331"
    assert body["custitem_sanmar_inventory_key"] == "92033"
    assert body["custitem_sanmar_size_index"] == "1"
    assert body["upcCode"] == "00882849000011"
    assert body["custitem_sanmar_case_size"] == 36
    assert body["weight"] == 0.55
    assert body["weightUnit"] == "lb"
    assert body["custitem_sanmar_front_image_url"].endswith("black_model_front.jpg")


def test_child_payload_marks_discontinued(sdl_n_path):
    tee = next(s for s in parse_styles(sdl_n_path) if s.style == "2000")
    body = build_child_payload(tee.skus[0], tee, _config())
    assert body["isInactive"] is True


def test_price_body_maps_levels(sdl_n_path):
    style = _k420(sdl_n_path)
    sku = next(s for s in style.skus if s.color_name == "Black" and s.size == "S")
    body = build_price_body(sku, _config())
    levels = {ln["priceLevel"]["id"]: ln["price"] for ln in body["price"]["items"]}
    assert levels["1"] == 9.99  # base = piece price
    assert levels["5"] == 8.49  # case level
    assert levels["6"] == 18.00  # msrp level
    assert body["custitem_sanmar_map"] == 16.20


def test_live_price_applies_active_sale(dip_path):
    records = {r.unique_key: r for r in parse_inventory(dip_path)}
    from datetime import datetime

    body = build_live_price_body(
        records["920331"], _config(), now=datetime(2026, 6, 15)
    )
    base = next(
        ln["price"] for ln in body["price"]["items"] if ln["priceLevel"]["id"] == "1"
    )
    assert base == 8.99  # each_sale_price wins inside the sale window


def test_live_price_outside_sale_window_uses_regular(dip_path):
    records = {r.unique_key: r for r in parse_inventory(dip_path)}
    from datetime import datetime

    body = build_live_price_body(
        records["920331"], _config(), now=datetime(2026, 7, 15)
    )
    base = next(
        ln["price"] for ln in body["price"]["items"] if ln["priceLevel"]["id"] == "1"
    )
    assert base == 9.99  # back to regular piece price after sale ends


def test_availability_body(dip_path):
    records = {r.unique_key: r for r in parse_inventory(dip_path)}
    body = build_availability_body(records["920331"], _config())
    assert body["custitem_sanmar_qty_available"] == 165
    breakdown = json.loads(body["custitem_sanmar_qty_by_whse"])
    assert breakdown == {"1": 120, "3": 45}

"""Which custom item fields the vendor writers still read and write."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import field_policy  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(field_policy, "MODE", "minimal")
    monkeypatch.setattr(field_policy, "_exists", {})


@pytest.mark.parametrize("field", [
    "cost", "upcCode", "manufacturer", "weight", "price",
    "custitem_bsg_on_sale", "custitem_feed_last_seen", "custitem_feed_source",
    "custitem_atlas_item_image", "custitem_sanmar_unique_key",
    "custitem_sanmar_style", "custitem_ss_sku", "custitem_ss_brand",
    "custitem_mtec_item_sku", "custitem_ua_part_id", "custitem_ua_style",
    "custitem_champro_part_id",
])
def test_kept(field):
    assert field_policy.keep(field)


@pytest.mark.parametrize("field", [
    "custitem_sanmar_qty_available", "custitem_sanmar_qty_cincinnati",
    "custitem_sanmar_msrp", "custitem_sanmar_map", "custitem_sanmar_status",
    "custitem_sanmar_front_image_url", "custitem_sanmar_is_closeout",
    "custitem_ss_qty_available", "custitem_ss_piece_price", "custitem_ss_gtin",
    "custitem_mtec_qty_available", "custitem_mtec_msrp", "custitem_ua_gtin",
    "custitem_ua_qty_available", "custitem_bsgshop_image_url",
])
def test_dropped(field):
    assert not field_policy.keep(field)


def test_all_mode_keeps_everything(monkeypatch):
    monkeypatch.setattr(field_policy, "MODE", "all")
    assert field_policy.keep("custitem_sanmar_qty_available")


class _Client:
    """An account that has only the fields in ``have``."""

    def __init__(self, have):
        self.have = {h.lower() for h in have}
        self.queries = 0

    def suiteql(self, query, **_kw):
        self.queries += 1
        cols = query.split("SELECT ")[1].split(" FROM")[0].split(", ")
        if any(c.strip().lower() not in self.have for c in cols):
            raise RuntimeError("NetSuite 400: Bad Request")
        return [{}]


def test_usable_drops_reference_fields_and_fields_not_in_the_account():
    client = _Client({"custitem_bsg_on_sale", "custitem_sanmar_style"})
    asked = ["custitem_ss_brand", "custitem_sanmar_style", "custitem_sanmar_msrp",
             "custitem_bsg_on_sale", "custitem_feed_last_seen", "custitem_sanmar_style"]
    assert field_policy.usable(client, asked) == [
        "custitem_sanmar_style", "custitem_bsg_on_sale"]


def test_usable_asks_once_when_everything_exists():
    have = {"custitem_bsg_on_sale", "custitem_feed_last_seen", "custitem_feed_source"}
    client = _Client(have)
    assert field_policy.usable(client, sorted(have)) == sorted(have)
    assert client.queries == 1
    field_policy.usable(client, sorted(have))
    assert client.queries == 1  # cached


def test_select():
    assert field_policy.select([]) == ""
    assert field_policy.select(["a", "b"]) == ", a, b"


def test_clean_keeps_native_fields_and_only_usable_custom_ones():
    body = {"cost": 4.1, "upcCode": "1", "custitem_bsg_on_sale": True,
            "custitem_sanmar_qty_available": 12, "custitem_feed_last_seen": "2026-11-20"}
    assert field_policy.clean(body, ["custitem_bsg_on_sale"]) == {
        "cost": 4.1, "upcCode": "1", "custitem_bsg_on_sale": True}
    assert field_policy.clean({"custitem_sanmar_qty_dallas": 3}, []) == {}


def test_new_items_are_born_with_identifiers_only():
    import momentec_create
    import ss_create

    ss = ss_create.child_fields({
        "sku": "B1", "style_id": "9", "style_name": "5000", "gtin": "0081",
        "brand_name": "Gildan", "msrp": 9.5, "piece_price": 3.1})
    assert ss == {"custitem_ss_sku": "B1", "custitem_ss_style": "5000",
                  "custitem_ss_brand": "Gildan"}
    assert ss_create.child_fields.__module__ == "ss_create"
    assert momentec_create.field_policy is field_policy

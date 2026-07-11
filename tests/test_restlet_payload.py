from __future__ import annotations

from decimal import Decimal

from sanmar_netsuite.models import SkuRecord, StyleRecord
from sanmar_netsuite.transform.restlet_payload import (
    build_child_payload,
    iter_child_payloads,
)

ACCOUNTS = dict(
    income_account="4100", cogs_account="5100", asset_account="1200", tax_schedule="Taxable"
)


def _sku(style: str, color: str, size: str, *, key: str, msrp=None, cost=None) -> SkuRecord:
    return SkuRecord(
        unique_key=key,
        inventory_key=key,
        size_index="1",
        style=style,
        color_name=color,
        mainframe_color=color[:4],
        size=size,
        description=f"{style} {color} {size}",
        piece_price=cost,
        msrp=msrp,
    )


def _style(style: str, *skus: SkuRecord) -> StyleRecord:
    return StyleRecord(
        style=style,
        title=f"Gildan {style} Tee",
        description="A tee",
        brand="Gildan",
        category="Tee Shirts",
        product_status="Active",
        subcategory="",
        skus=list(skus),
    )


def test_numeric_style_payload_passes_style_name_as_parent_ref():
    sku = _sku("2000", "Classic Navy", "S", key="111", msrp=Decimal("6.00"), cost=Decimal("3.49"))
    payload = build_child_payload(sku, _style("2000", sku), **ACCOUNTS)

    # The RESTlet resolves the parent by *name*, so the numeric style is passed
    # through verbatim — no internal-id juggling needed on this path.
    assert payload["style"] == "2000"
    assert payload["externalId"] == "SANMAR-111"
    assert payload["itemId"] == "2000-Classic Navy-Small"  # size normalized S -> Small
    assert payload["color"] == "Classic Navy"
    assert payload["size"] == "Small"
    assert payload["basePrice"] == 6.00
    assert payload["cost"] == 3.49
    assert payload["incomeAccount"] == "4100"
    assert payload["class"] == "Tops : Tees"  # mapped from the "Tee Shirts" category


def test_missing_price_fields_are_none_not_zero():
    sku = _sku("2000", "Black", "M", key="222")  # no msrp/cost
    payload = build_child_payload(sku, _style("2000", sku), **ACCOUNTS)
    assert payload["basePrice"] is None
    assert payload["cost"] is None


def test_iter_filters_by_style_and_respects_limit():
    s2000 = _style(
        "2000",
        _sku("2000", "Black", "S", key="1"),
        _sku("2000", "Black", "M", key="2"),
        _sku("2000", "Black", "L", key="3"),
    )
    k420 = _style("K420", _sku("K420", "Navy", "S", key="9"))

    one = list(iter_child_payloads([s2000, k420], style_filter="2000", limit=1, **ACCOUNTS))
    assert len(one) == 1
    assert one[0]["externalId"] == "SANMAR-1"

    # case-insensitive filter, and K420 children are excluded
    only_2000 = list(iter_child_payloads([s2000, k420], style_filter="2000", **ACCOUNTS))
    assert {p["style"] for p in only_2000} == {"2000"}
    assert len(only_2000) == 3

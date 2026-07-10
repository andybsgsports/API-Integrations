from __future__ import annotations

import re

from sanmar_netsuite.models import SkuRecord, StyleRecord
from sanmar_netsuite.netsuite.adopt import match_existing, split_color_size


class FakeClient:
    """Returns canned item rows for the style in a `WHERE vendorname = '..'` query."""

    def __init__(self, items_by_style: dict[str, list[dict]]) -> None:
        self.items_by_style = items_by_style

    def suiteql(self, query: str, **_kw):
        m = re.search(r"vendorname = '([^']*)'", query)
        return self.items_by_style.get(m.group(1) if m else "", [])


def _sku(style, color, mf_color, size, key, gtin="") -> SkuRecord:
    return SkuRecord(
        unique_key=key, inventory_key=key, size_index="1", style=style,
        color_name=color, mainframe_color=mf_color, size=size,
        description="d", gtin=gtin,
    )


def _style(style, *skus) -> StyleRecord:
    return StyleRecord(
        style=style, title="t", description="d", brand="b", category="c",
        product_status="Active", subcategory="", skus=list(skus),
    )


def test_split_color_size():
    assert split_color_size("2000-Crnslk-Large", "2000") == ("Crnslk", "Large")
    assert split_color_size("PC450-Ath Ro-2X-Large", "PC450") == ("Ath Ro", "2X-Large")
    assert split_color_size("2000", "2000") is None  # parent
    assert split_color_size("K420-Black-Small", "K420") == ("Black", "Small")


def test_matches_by_mainframe_color_and_normalized_size():
    styles = [_style("2000", _sku("2000", "Cornsilk", "Crnslk", "L", "111", gtin="00821780000019"))]
    client = FakeClient({"2000": [
        {"id": "100102", "itemid": "2000"},                 # parent -> ignored
        {"id": "500", "itemid": "2000-Crnslk-Large"},       # abbreviated color
    ]})
    report = match_existing(client, styles)
    row = report.rows[0]
    assert row.ns_id == "500"
    assert row.method == "mainframe"
    assert row.gtin == "00821780000019"  # carried for the UPC back-fill


def test_falls_back_to_full_color():
    styles = [_style("2000", _sku("2000", "Purple", "Pur", "M", "222"))]
    client = FakeClient({"2000": [{"id": "600", "itemid": "2000-Purple-Medium"}]})
    report = match_existing(client, styles)
    assert report.rows[0].ns_id == "600"
    assert report.rows[0].method == "fullcolor"


def test_unmatched_and_missing_and_dup_parents():
    styles = [
        _style("2000",
               _sku("2000", "Ash", "Ash", "L", "a"),        # matches
               _sku("2000", "Neon", "Neon", "S", "b")),      # no such child -> unmatched
        _style("NOPE", _sku("NOPE", "Black", "Black", "L", "c")),  # style absent in NS
    ]
    client = FakeClient({
        "2000": [
            {"id": "1", "itemid": "2000"},
            {"id": "2", "itemid": "2000"},                   # duplicate parent
            {"id": "3", "itemid": "2000-Ash-Large"},
        ],
        # "NOPE" returns nothing
    })
    report = match_existing(client, styles)
    assert len(report.matched) == 1
    assert len(report.unmatched) == 2  # 2000-Neon-Small + all of NOPE
    assert report.dup_parents.get("2000") == ["1", "2"]
    assert "NOPE" in report.missing_styles
    assert "1/3" in report.summary() or "matched" in report.summary()

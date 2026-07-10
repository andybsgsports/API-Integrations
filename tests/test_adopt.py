from __future__ import annotations

import re

from sanmar_netsuite.models import SkuRecord, StyleRecord
from sanmar_netsuite.netsuite.adopt import (
    heuristic_abbrev,
    match_existing,
    rename_is_safe,
    split_color_size,
)


class FakeClient:
    """Routes the three query shapes the matcher issues.

    * color list  -> rows with id/name/abbreviation
    * size list   -> rows with id/name
    * per-style   -> item rows for `WHERE vendorname = '<style>'`
    """

    def __init__(self, colors=None, sizes=None, items_by_style=None) -> None:
        self.colors = colors or []
        self.sizes = sizes or []
        self.items_by_style = items_by_style or {}

    def suiteql(self, query: str, **_kw):
        if "customlist_bsg_matrix_color" in query:
            return self.colors
        if "customlist_bsg_matrix_size" in query:
            return self.sizes
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


def test_heuristic_abbrev():
    assert heuristic_abbrev("California Blue") == "cabl"
    assert heuristic_abbrev("Forest Green") == "fogr"
    assert heuristic_abbrev("Ash") == "ash"  # single word: whole word


def test_option_match_by_heuristic_abbreviation():
    # NS item named 29M-Cabl-Small references color value 1016 ("Cabl") and
    # size value 21 ("Small"). Feed says California Blue / "California Blu".
    client = FakeClient(
        colors=[{"id": "1016", "name": "Cabl", "abbreviation": "Cabl"}],
        sizes=[{"id": "21", "name": "Small"}],
        items_by_style={"29M": [
            {"id": "9", "itemid": "29M", "color": "", "size": ""},  # parent
            {"id": "10", "itemid": "29M-Cabl-Small", "color": "1016", "size": "21"},
        ]},
    )
    styles = [_style("29M", _sku("29M", "California Blue", "California Blu", "S", "1",
                                 gtin="00012345678905"))]
    report = match_existing(client, styles)
    row = report.rows[0]
    assert row.ns_id == "10"
    assert row.method == "option:heur"
    # And the color list value is flagged for a full-name rename.
    assert report.color_renames["1016"] == ("Cabl", "California Blue")


def test_option_match_by_name_needs_no_rename():
    client = FakeClient(
        colors=[{"id": "7452", "name": "Classic Navy", "abbreviation": "Cn"}],
        sizes=[{"id": "22", "name": "Medium"}],
        items_by_style={"K420": [
            {"id": "30", "itemid": "K420-Cn-Medium", "color": "7452", "size": "22"},
        ]},
    )
    styles = [_style("K420", _sku("K420", "Classic Navy", "Cn", "M", "2"))]
    report = match_existing(client, styles)
    assert report.rows[0].ns_id == "30"
    assert report.rows[0].method == "option:name"
    assert report.color_renames == {}


def test_rename_is_safe_rules():
    feed = {"black", "black/ black", "carolina blue", "teal green"}
    # Clean abbreviation -> full name: allowed.
    assert rename_is_safe("Cabl", "Carolina Blue", feed)
    # Current name IS a real SanMar color: never rename it to another color.
    assert not rename_is_safe("Black", "Black/ Black", feed)
    # Multi-word current names are real colors (maybe another vendor's).
    assert not rename_is_safe("Team Grey", "Teal Green", feed)
    assert not rename_is_safe("Royal Cardinal", "Royal Caribe", feed)
    # Whitespace/case-only differences: nothing worth renaming.
    assert not rename_is_safe("Black/Red", "Black/ Red", feed)
    assert not rename_is_safe("", "Carolina Blue", feed)


def test_real_color_never_added_to_rename_plan():
    # "Black/ Black" matches the value named "Black" via the mainframe name;
    # the guard must keep "Black" out of the rename plan because plain Black
    # is itself a SanMar color elsewhere in the feed.
    client = FakeClient(
        colors=[{"id": "1", "name": "Black", "abbreviation": ""}],
        sizes=[{"id": "23", "name": "Large"}],
        items_by_style={"BG100": [
            {"id": "70", "itemid": "BG100-Black-Large", "color": "1", "size": "23"},
        ]},
    )
    styles = [
        _style("BG100", _sku("BG100", "Black/ Black", "Black", "L", "1")),
        _style("2000", _sku("2000", "Black", "Black", "L", "2")),
    ]
    report = match_existing(client, styles)
    assert report.rows[0].ns_id == "70"  # the item match itself still works
    assert report.color_renames == {}   # ...but no rename is proposed


def test_name_parse_fallback_when_option_fields_absent():
    # Items without option ids still match via STYLE-COLOR-SIZE name parsing.
    client = FakeClient(
        colors=[], sizes=[],
        items_by_style={"2000": [
            {"id": "500", "itemid": "2000-Crnslk-Large"},
        ]},
    )
    styles = [_style("2000", _sku("2000", "Cornsilk", "Crnslk", "L", "3"))]
    report = match_existing(client, styles)
    assert report.rows[0].ns_id == "500"
    assert report.rows[0].method == "mainframe"


def test_ns_side_coverage_counts_existing_children():
    client = FakeClient(
        colors=[{"id": "1", "name": "Ash", "abbreviation": "Ash"}],
        sizes=[{"id": "21", "name": "Small"}, {"id": "23", "name": "Large"}],
        items_by_style={"2000": [
            {"id": "90", "itemid": "2000", "color": "", "size": ""},        # parent
            {"id": "91", "itemid": "2000-Ash-Small", "color": "1", "size": "21"},
            {"id": "92", "itemid": "2000-Ash-Large", "color": "1", "size": "23"},
        ]},
    )
    # Feed only carries Ash/Small -> one of two existing children claimed.
    styles = [_style("2000", _sku("2000", "Ash", "Ash", "S", "4"))]
    report = match_existing(client, styles)
    assert report.ns_children_total == 2
    assert report.ns_children_claimed == 1
    assert "1/2" in report.summary()


def test_unmatched_missing_and_dup_parents():
    styles = [
        _style("2000",
               _sku("2000", "Ash", "Ash", "L", "a"),         # matches by name parse
               _sku("2000", "Neon", "Neon", "S", "b")),       # no such child
        _style("NOPE", _sku("NOPE", "Black", "Black", "L", "c")),  # style absent
    ]
    client = FakeClient(items_by_style={
        "2000": [
            {"id": "1", "itemid": "2000"},
            {"id": "2", "itemid": "2000"},                    # duplicate parent
            {"id": "3", "itemid": "2000-Ash-Large"},
        ],
    })
    report = match_existing(client, styles)
    assert len(report.matched) == 1
    assert len(report.unmatched) == 2
    assert report.dup_parents.get("2000") == ["1", "2"]
    assert "NOPE" in report.missing_styles

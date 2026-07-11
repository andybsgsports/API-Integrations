from __future__ import annotations

import re

from momentec_netsuite.adopt import clean_color, match_momentec
from momentec_netsuite.models import MomentecSku, MomentecStyle


class FakeClient:
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


def _sku(item_sku, color, size) -> MomentecSku:
    style = item_sku.split(".")[0]
    return MomentecSku(
        parent_sku=style.lstrip("0") or style, item_sku=item_sku,
        upc="711293000000", gtin="00711293000000", name="n", brand="60",
        division="ASI", description="d", category="c", msrp="31.8",
        cost="15.9", currency="USD", color=color, size=size, color_hex="",
        status="20", main_image_url="", swatch_image_url="", weight="",
        case_pack_qty="", country_of_origin="",
    )


def _style(*skus) -> MomentecStyle:
    return MomentecStyle(parent_sku=skus[0].parent_sku, skus=list(skus))


def test_clean_color_strips_brand_tag():
    assert clean_color("GRAPHITE (BA)") == "GRAPHITE"
    assert clean_color("BLACK HEATHER") == "BLACK HEATHER"
    assert clean_color(" J.NAVY ") == "J.NAVY"


def test_match_by_option_ids_with_leading_zero_style():
    # Feed Parent_SKU drops the leading zero but Item_SKU keeps it; the
    # matcher must query vendorname with the Item_SKU prefix (020000).
    client = FakeClient(
        colors=[{"id": "9", "name": "Black", "abbreviation": ""}],
        sizes=[{"id": "40", "name": "S/M"}],
        items_by_style={"020000": [
            {"id": "70", "itemid": "020000", "color": "", "size": ""},
            {"id": "71", "itemid": "020000-Black-S/M", "color": "9", "size": "40"},
        ]},
    )
    report = match_momentec(client, [_style(_sku("020000.B080.14", "BLACK", "S/M"))])
    assert report.rows[0].ns_id == "71"
    assert report.ns_children_claimed == 1


def test_size_normalization_fallback():
    # List stores "2X-Large"; feed says "2XL" -> normalized lookup must hit.
    client = FakeClient(
        colors=[{"id": "9", "name": "Black", "abbreviation": ""}],
        sizes=[{"id": "41", "name": "2X-Large"}],
        items_by_style={"029HBM": [
            {"id": "80", "itemid": "x", "color": "9", "size": "41"},
        ]},
    )
    report = match_momentec(client, [_style(_sku("029HBM.BLK.2XL", "BLACK", "2XL"))])
    assert report.rows[0].ns_id == "80"


def test_unmatched_and_missing_style():
    client = FakeClient()
    report = match_momentec(client, [_style(_sku("ZZZZ.A.2XL", "RED", "2XL"))])
    assert report.matched == []
    assert "ZZZZ" in report.missing_styles

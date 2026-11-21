"""Link apply: only clean matches, only two fields, capped."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sanmar_link_apply as la  # noqa: E402
import vendor_pipeline as vp  # noqa: E402

from sanmar_netsuite.models import SkuRecord, StyleRecord  # noqa: E402

COLORS = [{"id": "1", "name": "Black", "abbreviation": ""}]
SIZES = [{"id": "10", "name": "Small"}, {"id": "11", "name": "Large"},
         {"id": "12", "name": "Medium"}, {"id": "13", "name": "X-Large"}]


class FakeClient:
    def __init__(self, items_by_style):
        self.items_by_style = items_by_style

    def suiteql(self, query: str, **_kw):
        if "customlist_bsg_matrix_color" in query:
            return COLORS
        if "customlist_bsg_matrix_size" in query:
            return SIZES
        m = re.search(r"vendorname = '([^']*)'", query)
        return self.items_by_style.get(m.group(1) if m else "", [])


def _sku(style, size, key, gtin):
    return SkuRecord(
        unique_key=key, inventory_key=key, size_index="1", style=style,
        color_name="Black", mainframe_color="Black", size=size,
        description="d", gtin=gtin)


def _style(style, *skus):
    return StyleRecord(
        style=style, title="t", description="d", brand="b", category="c",
        product_status="Active", subcategory="", skus=list(skus))


def _item(i, itemid, size, **kw):
    return {"id": i, "itemid": itemid, "color": "1", "size": size, **kw}


ITEMS = {"PC61": [
    _item("100", "PC61", ""),                                   # parent
    _item("101", "PC61-Black-Small", "10"),                     # clean
    _item("102", "PC61-Black-Large", "11"),                     # no vendor line
    _item("103", "PC61-Black-Medium", "12", upccode="111"),     # already linked
    _item("104", "PC61-Black-X-Large", "13", upccode="999"),    # other UPC
    _item("105", "INACTIVE-PC61-Black-Small", "10"),            # dup + inactive
]}
STYLES = [_style("PC61", _sku("PC61", "S", "k1", "001"),
                 _sku("PC61", "L", "k2", "002"),
                 _sku("PC61", "M", "k3", "111"),
                 _sku("PC61", "XL", "k4", "004"))]
VENDOR = {"101", "103", "104", "105"}


def test_only_clean_unlinked_items_are_planned():
    p = la.plan(FakeClient(ITEMS), STYLES, VENDOR, cap=0)
    # 101 ties with the INACTIVE duplicate, so it is not clean either.
    assert p["jobs"] == []
    assert p["already"] == 1
    assert [e["id"] for e in p["other_upc"]] == ["104"]


def test_clean_item_is_planned_with_its_upc_and_key():
    items = {"PC61": [r for r in ITEMS["PC61"] if r["id"] != "105"]}
    p = la.plan(FakeClient(items), STYLES, VENDOR, cap=0)
    assert [(e["id"], e["upc"], e["key"]) for e in p["jobs"]] == [
        ("101", "001", "k1")]


def test_cap_stops_early():
    items, styles = {}, []
    for n in range(5):
        st = f"S{n}"
        items[st] = [_item(str(200 + n), f"{st}-Black-Small", "10")]
        styles.append(_style(st, _sku(st, "S", f"k{n}", f"00{n}")))
    vend = {str(200 + n) for n in range(5)}
    old = la.STYLE_CHUNK
    la.STYLE_CHUNK = 2
    try:
        p = la.plan(FakeClient(items), styles, vend, cap=3)
    finally:
        la.STYLE_CHUNK = old
    assert len(p["jobs"]) == 3
    assert p["styles_checked"] == 4


def test_cap_default_and_phase(monkeypatch):
    monkeypatch.delenv("LINK_MAX_ITEMS", raising=False)
    monkeypatch.delenv("MAX_ITEMS", raising=False)
    assert la._cap() == 25
    monkeypatch.setenv("MAX_ITEMS", "0")
    assert la._cap() == 0
    assert "link-apply" not in vp.PHASES
    assert vp.PIPELINES["sanmar"]["link-apply"] == ["scripts/sanmar_link_apply.py"]


def test_writes_only_upc_and_key():
    src = (ROOT / "scripts" / "sanmar_link_apply.py").read_text()
    assert "externalId" not in src
    assert src.count('body[') == 1 and '"upcCode"' in src
    assert "update_record" not in src

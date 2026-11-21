"""The Sanmar link report: read-only, and honest about why items do not match."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sanmar_link_report as lr  # noqa: E402
import vendor_pipeline as vp  # noqa: E402

from sanmar_netsuite.models import SkuRecord, StyleRecord  # noqa: E402
from sanmar_netsuite.netsuite.adopt import OptionMaps, match_existing  # noqa: E402

COLORS = [
    {"id": "1", "name": "Black", "abbreviation": ""},
    {"id": "2", "name": "Mgrn", "abbreviation": ""},
    {"id": "3", "name": "Navy", "abbreviation": ""},
]
SIZES = [{"id": "10", "name": "Small"}, {"id": "11", "name": "Large"},
         {"id": "12", "name": "Youth Large"}]


class FakeClient:
    def __init__(self, items_by_style, vendor_items=()):
        self.items_by_style = items_by_style
        self.vendor_items = list(vendor_items)
        self.queries: list[str] = []

    def suiteql(self, query: str, **_kw):
        self.queries.append(query)
        if "customlist_bsg_matrix_color" in query:
            return COLORS
        if "customlist_bsg_matrix_size" in query:
            return SIZES
        if "MAX(id)" in query:
            return [{"m": "500"}]
        if "itemvendor" in query:
            return [{"item": i} for i in self.vendor_items]
        m = re.search(r"vendorname = '([^']*)'", query)
        return self.items_by_style.get(m.group(1) if m else "", [])

    def __getattr__(self, name):  # any write attempt fails the test
        raise AssertionError(f"link report called client.{name}")


def _sku(style, color, size, key, gtin="0001"):
    return SkuRecord(
        unique_key=key, inventory_key=key, size_index="1", style=style,
        color_name=color, mainframe_color=color, size=size,
        description="d", gtin=gtin)


def _style(style, *skus):
    return StyleRecord(
        style=style, title="t", description="d", brand="b", category="c",
        product_status="Active", subcategory="", skus=list(skus))


def _item(i, itemid, color, size, **kw):
    return {"id": i, "itemid": itemid, "color": color, "size": size, **kw}


def _run(items, styles, vendor_items):
    client = FakeClient(items, vendor_items)
    report = match_existing(client, styles)
    vend = lr.sanmar_vendor_items(client)
    return report, lr.analyse(report, OptionMaps(client), vend), vend


def test_reasons_and_clean_counts():
    items = {"PC61": [
        _item("100", "PC61", "", ""),                       # parent
        _item("101", "PC61-Black-Small", "1", "10"),        # clean
        _item("102", "PC61-Black-Large", "1", "11"),        # no Sanmar line
        _item("103", "PC61-Mgrn-Small", "2", "10"),         # colour unknown
        _item("104", "PC61-Black-Youth Large", "1", "12"),  # size unknown
        _item("105", "PC61-Navy-Small", "3", "10", isinactive="T"),
    ]}
    styles = [_style("PC61",
                     _sku("PC61", "Black", "S", "k1"),
                     _sku("PC61", "Black", "L", "k2"),
                     _sku("PC61", "Red", "S", "k3")),
              _style("NEW1", _sku("NEW1", "Black", "S", "k4"))]
    report, a, vend = _run(items, styles, ["101", "103", "104", "105"])

    assert a["clean"] == ["101"]
    assert a["not_sanmar"] == ["102"]
    assert a["reasons"] == {
        "colour not matched to a feed colour for this style": 1,
        "size not matched to a feed size for this style": 1,
        "inactive in NetSuite": 1,
    }
    assert a["colors"] == {"Mgrn": 1}
    assert a["sizes"] == {"Youth Large": 1}
    assert a["feed_new_style"] == 1
    assert a["feed_new_under_existing"] == 1
    text = "\n".join(lr.describe(report, a, vend))
    assert "ready to link" in text and "Mgrn (1)" in text
    assert all(line.startswith("LINK REPORT") for line in text.splitlines())


def test_duplicates_are_never_called_clean():
    items = {"K500": [
        _item("201", "K500-Black-Small", "1", "10"),
        _item("202", "K500-Black-Small-2", "1", "10"),
    ]}
    styles = [_style("K500", _sku("K500", "Black", "S", "k1"))]
    report, a, _ = _run(items, styles, ["201", "202"])
    assert report.ambiguous == {"k1": ["201", "202"]}
    assert a["clean"] == []


def test_csvs_written(tmp_path):
    items = {"PC61": [_item("101", "PC61-Black-Small", "1", "10"),
                      _item("103", "PC61-Mgrn-Small", "2", "10")]}
    styles = [_style("PC61", _sku("PC61", "Black", "S", "k1"))]
    report, a, vend = _run(items, styles, ["101"])
    matched, unmatched = lr.write_csvs(report, a, vend, tmp_path)
    assert "Clean" in matched.read_text()
    assert "Mgrn" in unmatched.read_text()


def test_script_has_no_write_call():
    tree = ast.parse((ROOT / "scripts" / "sanmar_link_report.py").read_text())
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not called & {"update_record", "create_record", "delete_record",
                         "post", "patch", "put", "delete", "restlet"}


def test_link_runs_only_on_request():
    assert "link" not in vp.PHASES
    assert vp.ON_REQUEST_PHASES == ("link", "link-apply")
    assert vp.PIPELINES["sanmar"]["link"] == ["scripts/sanmar_link_report.py"]
    assert vp._SUMMARY_LINE.search("LINK REPORT: not matched: 4")


def test_inactive_named_items_are_not_clean():
    items = {"L709": [_item("301", "INACTIVE-L709-Black-Small", "1", "10")]}
    styles = [_style("L709", _sku("L709", "Black", "S", "k1"))]
    _report, a, _ = _run(items, styles, ["301"])
    assert a["clean"] == [] and a["retired"] == ["301"]

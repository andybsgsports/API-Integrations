"""Momentec create path (Andy, 2026-11-18): new styles, missing colours and
sizes under existing parents, and skip-and-list for anything that doesn't fit."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import momentec_create as mc  # noqa: E402
import vendor_pipeline as vp  # noqa: E402

from momentec_netsuite.models import MomentecSku  # noqa: E402

COLORS = {"navy": "11", "white": "12", "black": "13"}
SIZES = {"small": "21", "medium": "22", "s": "21", "m": "22"}


class FakeOptions:
    available = True

    def color_candidates(self, full, _mainframe):
        cid = COLORS.get(full.lower())
        return [(cid, "option:name")] if cid else []

    def size_candidates(self, size):
        sid = SIZES.get(size.lower())
        return [sid] if sid else []


class FakeResolver:
    def __init__(self, retired=()):
        self.retired = {r.lower() for r in retired}

    def canonical_name(self, _list, name):
        return name

    def is_retired(self, _list, name):
        return name.lower() in self.retired

    def resolve(self, _list, name):
        return ("1", "existing")


def sku(color="NAVY (BA)", size="S", status="20", style="1234", code="N1",
        name="YOUTH B-CORE HOODIE", gtin="00190002000341"):
    return MomentecSku(
        parent_sku=style, item_sku=f"{style}.{code}.{size}", upc="190002000341",
        gtin=gtin, name=name, brand="10", division="ASI",
        description="* 100% polyester * Wicks moisture",
        category="Adult | TEES | TOPS FSG", msrp="24.80000", cost="12.40000",
        currency="USD", color=color, size=size, color_hex="", status=status,
        main_image_url="https://static.momentecbrands.com/product/x.jpg",
        swatch_image_url="", weight="0.38", case_pack_qty="36",
        country_of_origin="", size_chart_url="http://example.com/sizes",
        weight_unit="lb", ribbon="",
    )


def test_title_case_only_touches_all_caps_text():
    assert mc.title_case("SINGLE-PLY 7&quot; BASKETBALL SHORTS") == (
        'Single-Ply 7" Basketball Shorts')
    assert mc.title_case("YOUTH C2 LONG SLEEVE TEE") == "Youth C2 Long Sleeve Tee"
    assert mc.title_case("NexGen Wicking  Long Sleeve Tee") == (
        "NexGen Wicking Long Sleeve Tee")


def test_colour_drops_the_brand_tag_and_is_title_case():
    assert mc.color_name("NAVY/WHITE (BA)") == "Navy/White"
    assert mc.color_name("DARK GREEN") == "Dark Green"


def test_class_only_where_the_category_is_certain():
    assert mc.class_path("Adult | TEES | TOPS FSG") == "Tops : Tees"
    assert mc.class_path("Adult | POLOS | TOPS ASB") == "Tops : Polos"
    assert mc.class_path("Adult | BASEBALL | ON-FIELD TOPS") == ""
    # A fleece BOTTOM is a sweatpant: never classed as a sweatshirt.
    assert mc.class_path("Adult | FLEECE | BOTTOMS ASB") == ""


def test_a_style_netsuite_does_not_have_is_new():
    plan = mc.plan_style([sku(), sku(size="M")], [], FakeOptions(), {"20"})
    assert plan["kind"] == "new" and len(plan["missing"]) == 2


def test_only_active_status_rows_are_created():
    plan = mc.plan_style([sku(), sku(size="M", status="50")], [],
                         FakeOptions(), {"20"})
    assert [s.size for s in plan["missing"]] == ["S"]
    assert mc.plan_style([sku(status="50")], [], FakeOptions(), {"20"})["kind"] == (
        "complete")


PARENT = {"id": "900", "itemid": "1234", "color": None, "size": None}


def test_missing_size_is_added_under_the_existing_parent():
    rows = [PARENT, {"id": "901", "itemid": "1234-Navy-Small",
                     "color": "11", "size": "21"}]
    plan = mc.plan_style([sku(), sku(size="M")], rows, FakeOptions(), {"20"})
    assert plan["kind"] == "adopt" and plan["parent_id"] == "900"
    assert [s.size for s in plan["missing"]] == ["M"]


def test_nothing_missing_is_complete():
    rows = [PARENT, {"id": "901", "itemid": "x", "color": "11", "size": "21"}]
    assert mc.plan_style([sku()], rows, FakeOptions(), {"20"})["kind"] == "complete"


def test_an_existing_item_the_feed_cannot_explain_skips_the_style():
    # Child 902 is some colour the feed match did not claim: the "missing"
    # SKU could be that same item under another spelling.
    rows = [PARENT,
            {"id": "901", "itemid": "x", "color": "11", "size": "21"},
            {"id": "902", "itemid": "y", "color": "99", "size": "22"}]
    plan = mc.plan_style([sku(), sku(size="M")], rows, FakeOptions(), {"20"})
    assert plan["kind"] == "skip" and "could not be matched" in plan["reason"]


def test_a_closeout_row_still_explains_its_existing_item():
    rows = [PARENT,
            {"id": "901", "itemid": "x", "color": "11", "size": "21"},
            {"id": "902", "itemid": "y", "color": "13", "size": "21"}]
    feed = [sku(), sku(size="M"), sku(color="BLACK", code="B1", status="50")]
    plan = mc.plan_style(feed, rows, FakeOptions(), {"20"})
    assert plan["kind"] == "adopt"


@pytest.mark.parametrize("parents", [0, 2])
def test_no_single_parent_skips_the_style(parents):
    rows = [dict(PARENT, id=str(900 + i)) for i in range(parents)]
    rows.append({"id": "950", "itemid": "x", "color": "11", "size": "21"})
    plan = mc.plan_style([sku(), sku(size="M")], rows, FakeOptions(), {"20"})
    assert plan["kind"] == "skip" and "parent item(s)" in plan["reason"]


def test_child_is_born_in_andys_item_format():
    p = mc.child_payload(sku(), FakeResolver(), {}, parent_id="900")
    assert p["itemId"] == "1234-Navy-Small"
    assert p["externalId"] == "MTEC-1234.N1.S"
    assert p["displayName"] == p["description"] == "Youth B-Core Hoodie"
    assert p["cost"] == 10.54              # feed wholesale 12.40 less 15%
    assert p["basePrice"] == 24.8          # the feed's MSRP
    assert p["upc"] == "00190002000341"
    assert p["location"] == p["preferredLocation"] == "Badger Sporting Goods"
    assert p["manufacturer"] == "Augusta Sportswear"
    assert p["parentId"] == "900" and p["vendorName"] == "1234"
    assert p["fields"]["custitem_mtec_item_sku"] == "1234.N1.S"
    assert p["fields"]["custitem_mtec_size_guide"].startswith("https://")


def test_parent_carries_vendor_names_and_description():
    body = mc.parent_body("1234", [sku()], {}, {"location": {"id": "1"}})
    assert body["itemId"] == "1234" and body["matrixType"] == "PARENT"
    assert body["displayName"] == "Youth B-Core Hoodie"
    assert body["storeDescription"] == "- 100% polyester\n- Wicks moisture"
    assert body["itemVendor"]["items"][0]["preferredVendor"] is True
    assert body["manufacturer"] == "Augusta Sportswear"


def test_name_already_taken_is_a_skip_not_a_failure():
    class Client:
        def call_restlet(self, *_a):
            return {"results": [
                {"externalId": "MTEC-1", "status": "created"},
                {"externalId": "MTEC-2", "status": "error",
                 "message": "This item already exists."},
                {"externalId": "MTEC-3", "status": "error",
                 "message": "That combination of options already exists"},
                {"externalId": "MTEC-4", "status": "error", "message": "boom"},
            ]}

    class Cfg:
        class netsuite:  # noqa: N801
            matrix_script_id = matrix_deploy_id = "1"

    payloads = [{"externalId": f"MTEC-{i}", "itemId": f"I{i}"} for i in (1, 2, 3, 4)]
    created, already, failures, taken = mc.post_children(Client(), Cfg, payloads)
    assert (created, already, failures, taken) == (1, 1, 1, ["I2"])


def test_momentec_has_a_create_phase_that_is_not_in_the_chain():
    assert vp.PIPELINES["momentec"]["create"] == ["scripts/momentec_create.py"]
    nightly = (Path(__file__).resolve().parents[1]
               / ".github/workflows/nightly-pipeline.yml").read_text()
    assert 'NEXT="momentec";    NEXT_PHASES="update"' in nightly

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


def test_class_from_category_and_item_name():
    cp = mc.class_path
    assert cp("Adult | TEES | TOPS FSG") == "Tops : Tees"
    assert cp("Adult | POLOS | TOPS ASB") == "Tops : Polos"
    assert cp("Adult | BASEBALL | ON-FIELD TOPS", "Baseball Jersey") == "Uniforms : Jersey"
    assert cp("Adult | BASKETBALL | ON-FLD BOTTOMS", "Basketball Shorts") == (
        "Uniforms : Shorts")
    assert cp("Youth | BASEBALL | ON-FLD BOTTOMS", "Youth Baseball Knicker") == (
        "Uniforms : Pants")
    # A fleece BOTTOM is a sweatpant: never classed as a sweatshirt.
    assert cp("Adult | FLEECE | BOTTOMS ASB", "Closed Bottom Sweatpant") == (
        "Bottoms : Pants")
    assert cp("Adult | BOTTOMS | BOTTOMS FSG", "Mesh Shorts With Pockets") == (
        "Bottoms : Shorts")
    assert cp("Adult | FLEECE | TOPS ASB", "Fleece Hoodie") == "Tops : Hoods"
    assert cp("Adult | FLEECE | TOPS ASB", "Fleece Crew") == "Tops : Sweatshirts"
    assert cp("Adult | PULLOVERS | TOPS ASB") == "Outerwear : Pullovers"
    assert cp("Misc | HEADWEAR | HEADWEAR ASB") == "Uniforms : Headwear"
    assert cp("Adult | LIFESTYLE | TOPS ASB") == "Tops"
    assert cp("Misc | ACCESSORIES | ACCESSORIES FSG") == ""


@pytest.mark.parametrize("color", [
    "BT. ORANGE/WHITE", "COL BL/BLACK/WHITE", "HTHR GREY/LT CHARCOAL",
    "NBA NY KNICKS ROYAL/BT ORANGE", "PANTHER TEAL/BEIGE/PANTHER TEA",
    "GRAPH/SAFETY YELLOW (BA)", "BUCKHBUC",
])
def test_abbreviated_or_cut_off_colours_are_flagged(color):
    assert mc.abbreviated_color(color)


@pytest.mark.parametrize("color", [
    "NAVY/WHITE (BA)", "RED", "DARK GREEN/WHITE", "ATHLETIC HEATHER",
    "NBA MIAMI HEAT BLACK/WHITE", "GRAPHITE/NEON ORANGE/GRAPHITE", "SKY BLUE",
])
def test_spelled_out_colours_are_not_flagged(color):
    assert mc.abbreviated_color(color) == ""


def test_nba_keeps_its_capitals():
    assert mc.color_name("NBA MIAMI HEAT BLACK/WHITE") == "NBA Miami Heat Black/White"


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
                     "color": "11", "size": "21", "parent": "900"}]
    plan = mc.plan_style([sku(), sku(size="M")], rows, FakeOptions(), {"20"})
    assert plan["kind"] == "adopt" and plan["parent_id"] == "900"
    assert [s.size for s in plan["missing"]] == ["M"]


def test_nothing_missing_is_complete():
    rows = [PARENT, {"id": "901", "itemid": "x", "color": "11", "size": "21", "parent": "900"}]
    assert mc.plan_style([sku()], rows, FakeOptions(), {"20"})["kind"] == "complete"


def test_an_existing_item_the_feed_cannot_explain_skips_the_style():
    # Child 902 is some colour the feed match did not claim: the "missing"
    # SKU could be that same item under another spelling.
    rows = [PARENT,
            {"id": "901", "itemid": "x", "color": "11", "size": "21", "parent": "900"},
            {"id": "902", "itemid": "y", "color": "99", "size": "22", "parent": "900"}]
    plan = mc.plan_style([sku(), sku(size="M")], rows, FakeOptions(), {"20"})
    assert plan["kind"] == "skip" and "could not be matched" in plan["reason"]
    assert plan["reason"].endswith(": y")   # names the item so it can be found


def test_a_closeout_row_still_explains_its_existing_item():
    rows = [PARENT,
            {"id": "901", "itemid": "x", "color": "11", "size": "21", "parent": "900"},
            {"id": "902", "itemid": "y", "color": "13", "size": "21", "parent": "900"}]
    feed = [sku(), sku(size="M"), sku(color="BLACK", code="B1", status="50")]
    plan = mc.plan_style(feed, rows, FakeOptions(), {"20"})
    assert plan["kind"] == "adopt"


def test_parent_is_found_through_the_children():
    # The parent (id 777) does not carry the vendor code, so it is not in the
    # rows at all; the existing child points at it.
    rows = [{"id": "901", "itemid": "x", "color": "11", "size": "21",
             "parent": "777"}]
    plan = mc.plan_style([sku(), sku(size="M")], rows, FakeOptions(), {"20"})
    assert plan["kind"] == "adopt" and plan["parent_id"] == "777"


def test_duplicate_parents_use_the_one_holding_the_children():
    rows = [PARENT, dict(PARENT, id="899"),
            {"id": "901", "itemid": "x", "color": "11", "size": "21",
             "parent": "899"}]
    plan = mc.plan_style([sku(), sku(size="M")], rows, FakeOptions(), {"20"})
    assert plan["kind"] == "adopt" and plan["parent_id"] == "899"


def test_children_under_two_parents_skip_the_style():
    rows = [{"id": "901", "itemid": "x", "color": "11", "size": "21", "parent": "1"},
            {"id": "902", "itemid": "y", "color": "12", "size": "21", "parent": "2"}]
    feed = [sku(), sku(color="WHITE", code="W1"), sku(size="M")]
    plan = mc.plan_style(feed, rows, FakeOptions(), {"20"})
    assert plan["kind"] == "skip" and "2 different parents" in plan["reason"]


def test_two_empty_parents_skip_the_style():
    rows = [PARENT, dict(PARENT, id="899")]
    plan = mc.plan_style([sku()], rows, FakeOptions(), {"20"})
    assert plan["kind"] == "skip" and "none has children" in plan["reason"]


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
    assert "class" not in p   # the RESTlet's class lookup fails; set by id after
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
                {"externalId": "MTEC-1", "status": "created", "id": 501},
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
    created, already, failures, taken, ids = mc.post_children(Client(), Cfg, payloads)
    assert (created, already, failures, taken, ids) == (1, 1, 1, ["I2"], ["501"])


def test_class_is_set_by_id_on_the_created_children(monkeypatch):
    sent: list = []
    monkeypatch.setattr(mc, "write_records",
                        lambda client, rtype, jobs: (sent.extend(jobs), (len(jobs), 0))[1])
    assert mc.set_class(object(), ["501", "502"], "16") == 0
    assert sent == [("501", {"class": {"id": "16"}}), ("502", {"class": {"id": "16"}})]
    assert mc.set_class(object(), ["501"], None) == 0 and len(sent) == 2


def test_momentec_has_a_create_phase_that_is_not_in_the_chain():
    assert vp.PIPELINES["momentec"]["create"] == ["scripts/momentec_create.py"]
    nightly = (Path(__file__).resolve().parents[1]
               / ".github/workflows/nightly-pipeline.yml").read_text()
    assert 'NEXT="momentec";    NEXT_PHASES="update"' in nightly


def test_styles_are_fetched_in_batches_not_one_by_one():
    queries: list[str] = []

    class Client:
        def suiteql(self, q):
            queries.append(q)
            return [{"id": "1", "itemid": "x", "parent": "9", "vendorname": "a2",
                     "color": "11", "size": "21"}] if "'A2'" in q else []

    tokens = [f"A{i}" for i in range(90)]
    out = mc.fetch_style_items(Client(), tokens)
    assert len(queries) == 3                      # 90 styles, 40 per query
    assert [r["id"] for r in out["A2"]] == ["1"]  # matched whatever the case
    assert out["A5"] == []


def test_on_list_answers_from_the_index():
    class Resolver:
        calls = 0

        def _norm_index(self, _list):
            Resolver.calls += 1
            return {"navywhite": ("1", "Navy/White")}

    r = Resolver()
    assert mc.on_list(r, "colors", "Navy/ White") is True
    assert mc.on_list(r, "colors", "Bt. Orange") is False
    assert mc.on_list(r, "colors", "") is False

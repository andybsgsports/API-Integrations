"""S&S discover: every SKU lands in exactly one bucket, and 'exists' wins.

The dangerous failure mode is a SKU that already exists in NetSuite being
counted as new (a live create would then duplicate it), so the tests centre
on the two exists-detections: GTIN -> upcCode, and the normalised
colour/size combo under an ADOPTED parent.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from collections import Counter

from ss_create_preview import (
    brand_allowed,
    brand_allowlist,
    combo_key,
    excluded_brands,
)


def test_combo_key_normalises_colour_variants():
    # 'J. Navy' from the feed must collide with an existing 'J.Navy' child.
    assert combo_key("J. Navy", "S") == combo_key("J.Navy", "Small")


def test_combo_key_normalises_sizes_through_the_shared_size_rule():
    # The list stores spelled-out sizes; the S&S feed sends abbreviations.
    assert combo_key("Black", "S") == combo_key("Black", "Small")


def test_combo_key_folds_the_two_spellings_of_the_X_family():
    # A child stored as 'XX-Large' must collide with the feed's '2XL', or the
    # preview counts an existing child as new and queues a duplicate create.
    assert combo_key("Black", "2XL") == combo_key("Black", "XX-Large")
    assert combo_key("Black", "XXL") == combo_key("Black", "XX-Large")
    assert combo_key("Black", "3XL") == combo_key("Black", "XXX-Large")
    assert combo_key("Black", "XS") == combo_key("Black", "X-Small")


def test_the_fold_does_not_touch_the_shared_size_rule():
    # Folding lives in the preview because normalize_size's output is matched
    # against live list-value names by exact string; changing it would alter
    # every vendor's update path (OptionMaps.size_candidates).
    from sanmar_netsuite.transform.sizes import normalize_size
    assert normalize_size("XX-Large") == "XX-Large"
    assert normalize_size("2XL") == "2X-Large"


def test_single_x_sizes_are_not_folded_into_a_number():
    assert combo_key("Black", "XL") != combo_key("Black", "2XL")
    assert combo_key("Black", "XL") == combo_key("Black", "X-Large")


def test_combo_key_keeps_genuinely_different_children_apart():
    assert combo_key("Black", "Small") != combo_key("Black", "Medium")
    assert combo_key("Navy", "Small") != combo_key("J.Navy", "Small")


# --- brand allowlist: creation is scoped to brands BSG actually sells

def test_named_brands_match_ignoring_punctuation_and_case(monkeypatch):
    monkeypatch.setenv("SS_CREATE_BRANDS", "Bella+Canvas, Gildan")
    allowed = brand_allowlist()
    assert brand_allowed("BELLA + CANVAS", allowed)
    assert brand_allowed("gildan", allowed)
    assert not brand_allowed("Anvil", allowed)


def test_an_empty_allowlist_permits_every_brand(monkeypatch):
    monkeypatch.delenv("SS_CREATE_BRANDS", raising=False)
    allowed = brand_allowlist()
    assert allowed == set()
    assert brand_allowed("anything at all", allowed)


def test_CARRIED_resolves_to_the_brands_already_stocked(monkeypatch):
    # Andy chose "all 36 brands we carry today" -- a rule, not a frozen list,
    # so it resolves from the live catalogue each run.
    monkeypatch.setenv("SS_CREATE_BRANDS", "CARRIED")
    carried = Counter({"Gildan": 7952, "BELLA + CANVAS": 6919, "Augusta": 0})
    allowed = brand_allowlist(carried)
    assert brand_allowed("gildan", allowed)
    assert brand_allowed("Bella+Canvas", allowed)
    assert not brand_allowed("Augusta", allowed)      # stocked zero today
    assert not brand_allowed("Never Heard Of It", allowed)


def test_CARRIED_can_be_combined_with_explicitly_named_brands(monkeypatch):
    monkeypatch.setenv("SS_CREATE_BRANDS", "CARRIED, Augusta Sportswear")
    allowed = brand_allowlist(Counter({"Gildan": 10}))
    assert brand_allowed("Gildan", allowed)
    assert brand_allowed("augusta sportswear", allowed)


# --- brands bought direct from Momentec must never be created from S&S

def test_momentec_brands_are_excluded_even_when_carried(monkeypatch):
    # The trap in the CARRIED rule: Badger/Augusta/etc show a small carried
    # count because those items came from the MOMENTEC feed, and S&S then
    # offers the whole catalogue behind them (~42,700 SKUs across six brands).
    monkeypatch.setenv("SS_CREATE_BRANDS", "CARRIED")
    monkeypatch.delenv("SS_EXCLUDE_BRANDS", raising=False)
    carried = Counter({"Badger": 231, "Augusta Sportswear": 146,
                       "Holloway": 98, "Gildan": 7952})
    allowed, excluded = brand_allowlist(carried), excluded_brands()
    assert brand_allowed("Gildan", allowed, excluded)
    for direct in ("Badger", "Augusta Sportswear", "Holloway",
                   "Alleson Athletic", "Russell Athletic", "C2 Sport",
                   "High Five", "Pacific Headwear"):
        assert not brand_allowed(direct, allowed, excluded), direct


def test_every_momentec_supplied_brand_is_covered():
    # momentec_backfill.BRAND_NAMES is the source of truth for who Momentec
    # supplies; adding a brand code there must not silently leave that brand
    # exposed to S&S creation. S&S spells some differently ('C2 Sport').
    from momentec_backfill import BRAND_NAMES
    from ss_create_preview import MOMENTEC_DIRECT_BRANDS, _brand_key
    covered = {_brand_key(b) for b in MOMENTEC_DIRECT_BRANDS}
    missing = sorted({b for b in BRAND_NAMES.values()
                      if _brand_key(b) not in covered})
    assert not missing, f"Momentec-supplied brand(s) not excluded: {missing}"


def test_the_exclusion_can_be_overridden(monkeypatch):
    monkeypatch.setenv("SS_CREATE_BRANDS", "CARRIED")
    monkeypatch.setenv("SS_EXCLUDE_BRANDS", "")
    allowed, excluded = brand_allowlist(Counter({"Badger": 231})), excluded_brands()
    assert excluded == set()
    assert brand_allowed("Badger", allowed, excluded)


# --- the download must carry style titles, or created parents have no name

def test_download_writes_styles_alongside_products(tmp_path, monkeypatch):
    # /Products carries no style title or description, so a created matrix
    # PARENT would have nothing for Display Name / Store Description -- the
    # fields Andy's spec makes mandatory at creation.
    import argparse
    import json as _json

    from ss_activewear_netsuite import cli
    from ss_activewear_netsuite.models import SsProduct, SsStyle

    class _Client:
        def iter_products(self):
            yield SsProduct(sku="B123", style_id="7", style_name="3001C",
                            brand_name="BELLA + CANVAS", color_name="Black",
                            color_code="BLK", color_price_code="",
                            size_name="S", size_order="2")

        def iter_styles(self):
            yield SsStyle(style_id="7", style_name="3001C",
                          brand_name="BELLA + CANVAS",
                          title="Unisex Jersey Short Sleeve Tee",
                          description="Retail fit, 100% Airlume cotton.")

    monkeypatch.setattr(cli, "_client", lambda _cfg: _Client())
    out = tmp_path / "products.json"
    cli.cmd_download(argparse.Namespace(out=str(out)), object())

    styles = _json.loads((tmp_path / "styles.json").read_text())
    assert styles == [{
        "style_id": "7", "style_name": "3001C", "brand_name": "BELLA + CANVAS",
        "title": "Unisex Jersey Short Sleeve Tee",
        "description": "Retail fit, 100% Airlume cotton.",
        "category_name": "",
    }]
    assert _json.loads(out.read_text())[0]["sku"] == "B123"


# --- the create phase's payload shape

def test_name_fields_keep_the_style_code():
    from ss_create import display_name
    assert display_name("3001C", "Unisex Jersey Tee") == "Unisex Jersey Tee 3001C"
    # No title in the feed -> the code alone, never a blank name.
    assert display_name("3001C", "") == "3001C"


def test_relative_image_paths_become_absolute_urls():
    # URL-type fields reject a relative path and the error names no field --
    # the failure mode that rejected all 45,239 SanMar records on 2026-08-05.
    from ss_create import image_url
    assert image_url("Images/Color/1_f.jpg") == \
        "https://cdn.ssactivewear.com/Images/Color/1_f.jpg"
    assert image_url("https://x.test/a.jpg") == "https://x.test/a.jpg"
    assert image_url("") is None


def test_a_child_is_born_with_its_ss_data():
    from ss_create import child_fields
    fields = child_fields({
        "sku": "B123", "style_name": "3001C", "color_name": "Black",
        "gtin": "00812345", "brand_name": "BELLA + CANVAS", "msrp": "12.50",
        "front_image_url": "Images/1_f.jpg", "size_name": "",
    })
    assert fields["custitem_ss_sku"] == "B123"
    assert fields["custitem_ss_gtin"] == "00812345"
    assert fields["custitem_ss_msrp"] == "12.50"
    assert fields["custitem_ss_front_image_url"].startswith("https://")
    assert "custitem_ss_size_name" not in fields   # blanks are dropped


def test_richardson_is_excluded_as_sanmar_sourced(monkeypatch):
    # Andy approved the exclusion 2026-08-11: Richardson arrives via SanMar,
    # so S&S creation would duplicate a supply line BSG already has.
    monkeypatch.delenv("SS_EXCLUDE_BRANDS", raising=False)
    excluded = excluded_brands()
    assert not brand_allowed("Richardson", set(), excluded)
    assert not brand_allowed("RICHARDSON", set(), excluded)


def test_store_description_html_becomes_readable_text():
    # S&S descriptions arrive as raw HTML; written verbatim they render as
    # markup soup in Store Description (Andy, 2026-08-12: "looks funky").
    from ss_create import clean_html
    raw = ('<ul><li><span style="font-family: Gotham-Book;">65% Polyester/ '
           '35% Cotton</span></li><li>Double-breasted reversible front</li>'
           '<li>Ten non-yellowing&nbsp;UV buttons</li></ul><div><br /></div>')
    assert clean_html(raw) == (
        "- 65% Polyester/ 35% Cotton\n"
        "- Double-breasted reversible front\n"
        "- Ten non-yellowing\xa0UV buttons")
    assert clean_html("") == ""
    assert clean_html("plain text stays") == "plain text stays"


def test_style_category_comes_from_baseCategory():
    # S&S has no "categoryName" -- probe run 31656733104 found 0 of 5,653
    # styles carrying it, which is why every S&S item came out classless.
    # /Styles calls the garment type `baseCategory`.
    from ss_activewear_netsuite.ss_activewear.client import style_from_payload
    s = style_from_payload({
        "styleID": 9182, "styleName": "112", "brandName": "Richardson",
        "title": "Trucker Cap", "baseCategory": "Headwear",
        "categories": "11,71,87,155",
    })
    assert s.category_name == "Headwear"


def test_baseCategory_maps_to_a_netsuite_class():
    from sanmar_netsuite.transform.csv_export import class_for_category
    # The vocabulary S&S uses lands on the same keyword table SanMar's does.
    assert class_for_category("Headwear") == "Uniforms : Headwear"
    assert class_for_category("T-Shirts") == "Tops : Tees"
    assert class_for_category("Polos") == "Tops : Polos"
    assert class_for_category("Fleece") == "Tops : Sweatshirts"
    assert class_for_category("Outerwear") == "Outerwear : Jackets"
    assert class_for_category("Bags") == "Bags"

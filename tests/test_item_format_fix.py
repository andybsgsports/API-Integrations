"""Andy's item-format rules for EXISTING items (sandbox review, 2026-11-11).

The corrections rewrite fields on ~100k items, so the rules that decide WHAT
gets written are pinned here; the NetSuite plumbing around them is thin.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from item_format_fix import (  # noqa: E402
    MATRIX_NAME_TEMPLATE,
    display_target,
    strip_style_suffix,
    team_price,
    template_needs_fix,
    vendor_lines_with_price,
)


def test_trailing_style_code_is_stripped_from_the_display_name():
    assert strip_style_suffix("Sport-Tek 1/2-Zip Wind Shirt JST75", "JST75") \
        == "Sport-Tek 1/2-Zip Wind Shirt"
    # Case-insensitive, and tolerant of the punctuation feeds put before it.
    assert strip_style_suffix("Port & Co Core Cotton Tee. pc54", "PC54") \
        == "Port & Co Core Cotton Tee"


def test_a_code_that_is_not_a_trailing_word_is_left_alone():
    # Mid-name, part of a longer word, or the whole name: never touched --
    # a Display Name is not emptied.
    assert strip_style_suffix("PC54 Core Cotton Tee", "PC54") == "PC54 Core Cotton Tee"
    assert strip_style_suffix("Core Cotton TeePC54", "PC54") == "Core Cotton TeePC54"
    assert strip_style_suffix("PC54", "PC54") == "PC54"
    assert strip_style_suffix("Core Cotton Tee", "") == "Core Cotton Tee"


def test_display_name_becomes_the_description_when_it_is_the_same_title():
    # The live DT2500 case.
    assert display_target(
        "District - Juniors Cotton Swing Tank DT2500",
        "District - Juniors Cotton Swing Tank", "DT2500",
    ) == "District - Juniors Cotton Swing Tank"


def test_a_truncated_display_name_is_completed_from_the_description():
    desc = "Sport-Tek Youth PosiCharge Mesh Reversible Spliced Short"
    # The old 60-character cut landed inside the code (live: style YT565).
    assert display_target(desc + " YT5", desc, "YT565") == desc
    # ...or inside the title itself.
    assert display_target(desc[:50] + " YT565", desc, "YT565") == desc


def test_a_last_word_that_only_looks_like_the_code_is_kept():
    # "ST" starts style ST650, but the description does not confirm the rest
    # is the title, so nothing is cut.
    assert display_target("Dry Zone ST", "Marketing copy.", "ST650") == "Dry Zone ST"
    # One stray letter is never treated as a truncated code.
    assert display_target("Tee S", "Tee S", "S100") == "Tee S"


def test_an_unrelated_or_overlong_description_only_strips_the_code():
    assert display_target("Fleece Hoodie F100", "Marketing copy about fleece.", "F100") \
        == "Fleece Hoodie"
    long_desc = "Fleece Hoodie " + "x" * 80
    assert display_target("Fleece Hoodie F100", long_desc, "F100") == "Fleece Hoodie"


def test_only_two_option_templates_in_the_wrong_order_are_rewritten():
    assert template_needs_fix("{itemid}-{custitem_bsg_size}-{custitem_bsg_color}")
    assert not template_needs_fix(MATRIX_NAME_TEMPLATE)
    # Single-option parents and hand-written oddities are reported, not changed.
    assert not template_needs_fix("{itemid}-{custitem_bsg_size}")
    assert not template_needs_fix("1389660-{custitem_bsg_color}-{custitem_bsg_size}")
    assert not template_needs_fix(None)


def test_team_price_is_ninety_percent_of_base_to_the_cent():
    assert team_price(6.00) == 5.40            # PC54, as the older items carry
    assert team_price("6.98") == 6.28          # DT2500 Small
    assert team_price(12.98) == 11.68
    assert team_price(0.05) == 0.05            # 0.045 rounds half up
    assert team_price(None) is None and team_price(0) is None


def _line(vendor, preferred, price=None, code="DT2500"):
    return {"vendor": vendor, "preferredvendor": "T" if preferred else "F",
            "purchaseprice": price, "vendorcode": code}


def test_blank_preferred_vendor_price_is_filled_from_cost():
    lines = vendor_lines_with_price([_line(512, True)], 3.49)
    assert lines == [{"vendor": {"id": "512"}, "preferredVendor": True,
                      "vendorCode": "DT2500", "purchasePrice": 3.49}]


def test_existing_vendor_prices_are_never_overwritten():
    # Preferred line already priced -> nothing to do at all.
    assert vendor_lines_with_price([_line(512, True, price=2.99)], 3.49) is None
    # A second, non-preferred line is carried over exactly as it is.
    lines = vendor_lines_with_price(
        [_line(512, True), _line(510, False, price=4.10, code="")], 3.49)
    assert lines[1] == {"vendor": {"id": "510"}, "preferredVendor": False,
                        "purchasePrice": 4.10}
    assert lines[0]["purchasePrice"] == 3.49


def test_no_cost_or_no_preferred_line_means_no_write():
    assert vendor_lines_with_price([_line(512, True)], None) is None
    assert vendor_lines_with_price([_line(512, True)], 0) is None
    assert vendor_lines_with_price([_line(510, False)], 3.49) is None

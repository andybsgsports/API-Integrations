"""Tests for the SanMar Store Display Name / Store & Stock Description builders,
the AVAILABLE_SIZES feed mapping, and name parity with description_update."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sanmar_field_update import (  # noqa: E402
    is_closeout,
    store_description,
    store_display_name,
)


class TestDerivedCloseout:
    """BSG rule: discontinued AND still has stock = being cleared out.

    SanMar publishes no usable closeout signal (a full 161,271-SKU scan found
    'CloseOut' on exactly one), so this is our own definition.
    """

    def test_discontinued_with_stock_is_closeout(self):
        assert is_closeout("Discontinued", 42)

    def test_discontinued_without_stock_is_not(self):
        # Sold out and gone -- discontinued, but nothing left to clear.
        assert not is_closeout("Discontinued", 0)

    def test_discontinued_unknown_qty_is_not(self):
        # No inventory reading is not evidence of stock; stay conservative.
        assert not is_closeout("Discontinued", None)

    def test_active_with_stock_is_not(self):
        assert not is_closeout("Regular", 500)
        assert not is_closeout("Active", 500)
        assert not is_closeout("New", 500)

    def test_status_match_is_case_and_space_insensitive(self):
        assert is_closeout("  discontinued  ", 1)

    def test_blank_status_is_not(self):
        assert not is_closeout("", 10)
        assert not is_closeout(None, 10)  # type: ignore[arg-type]


def test_display_name_strips_trailing_style():
    assert store_display_name(
        "Nike Women's Club Fleece Sleeve Swoosh Pullover Hoodie NKFD9889", "NKFD9889"
    ) == "Nike Women's Club Fleece Sleeve Swoosh Pullover Hoodie"


def test_display_name_strips_dot_style():
    # SanMar OGIO titles append ". <style>"; the dash prefix stays.
    assert store_display_name("OGIO - Big Dome Duffel. 108087", "108087") == \
        "OGIO - Big Dome Duffel"


def test_display_name_titlecases_all_caps():
    assert store_display_name("AGGRESSIVE HEATHER TRUCKER SNAPBACK CAP", "106C") == \
        "Aggressive Heather Trucker Snapback Cap"


def test_display_name_strips_status_prefix():
    assert store_display_name("DISCONTINUED OGIO Transfer Duffel 108084", "108084") == \
        "OGIO Transfer Duffel"


def test_store_description_sizes_then_paragraph():
    assert store_description("Women's Sizes: S-2XL", "Built with versatility.") == \
        "Women's Sizes: S-2XL\n\nBuilt with versatility."


def test_store_description_one_size_has_no_sizes_line():
    # "One Size" has no ":" -> not a real size list -> copy only.
    assert store_description("One Size", "Large capacity duffel.") == "Large capacity duffel."


def test_store_description_missing_sizes():
    assert store_description("", "Just the paragraph.") == "Just the paragraph."


def test_store_description_missing_paragraph():
    assert store_description("Women's Sizes: S-2XL", "") == "Women's Sizes: S-2XL"


def test_available_sizes_parsed_from_feed():
    from sanmar_netsuite.sanmar.parsers import _SDL_ALIASES, _normalize_header
    assert _SDL_ALIASES[_normalize_header("AVAILABLE_SIZES")] == "available_sizes"


def test_store_display_name_matches_description_update_polish():
    # Store Display Name must equal the Display Name that description_update sets,
    # so the two jobs never disagree. Verify parity on representative titles.
    from description_update import _polish_name
    cases = [
        ("Nike Women's Club Fleece Sleeve Swoosh Pullover Hoodie NKFD9889", "NKFD9889"),
        ("OGIO - Big Dome Duffel. 108087", "108087"),
        ("AGGRESSIVE HEATHER TRUCKER SNAPBACK CAP", "106C"),
        ("District Women's Perfect Tri Tee", "DM130L"),
    ]
    for title, style in cases:
        assert store_display_name(title, style) == _polish_name(title, style)

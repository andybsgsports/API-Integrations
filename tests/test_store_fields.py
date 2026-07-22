"""Tests for the SanMar Store Display Name / Store Description builders and the
AVAILABLE_SIZES feed mapping."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sanmar_field_update import store_description, store_display_name  # noqa: E402


def test_display_name_strips_trailing_style():
    assert store_display_name(
        "Nike Women's Club Fleece Sleeve Swoosh Pullover Hoodie NKFD9889", "NKFD9889"
    ) == "Nike Women's Club Fleece Sleeve Swoosh Pullover Hoodie"


def test_display_name_style_case_insensitive():
    assert store_display_name("District Perfect Tri Tee dm130l", "DM130L") == \
        "District Perfect Tri Tee"


def test_display_name_no_trailing_style_unchanged():
    assert store_display_name("District Women's Perfect Tri Tee", "DM130L") == \
        "District Women's Perfect Tri Tee"


def test_store_description_sizes_then_paragraph():
    assert store_description("Women's Sizes: S-2XL", "Built with versatility.") == \
        "Women's Sizes: S-2XL\n\nBuilt with versatility."


def test_store_description_missing_sizes():
    assert store_description("", "Just the paragraph.") == "Just the paragraph."


def test_store_description_missing_paragraph():
    assert store_description("Women's Sizes: S-2XL", "") == "Women's Sizes: S-2XL"


def test_available_sizes_parsed_from_feed():
    # AVAILABLE_SIZES maps onto StyleRecord.available_sizes via the SDL aliases.
    from sanmar_netsuite.sanmar.parsers import _SDL_ALIASES, _normalize_header
    assert _SDL_ALIASES[_normalize_header("AVAILABLE_SIZES")] == "available_sizes"

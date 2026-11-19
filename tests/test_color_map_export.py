"""Vendor colour map export for the colour list cleanup."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import color_map_export as cme  # noqa: E402


def test_styles_are_matched_whatever_the_case():
    assert cme.wanted_styles("j333, Ss1000 ,,PC55T") == {"J333", "SS1000", "PC55T"}


def test_sanmar_rows_one_per_style_and_colour_with_the_short_code():
    styles = [
        NS(style="J333", brand="Port Authority", product_status="Active", skus=[
            NS(color_name="Dark Grey Heather", mainframe_color="DkGryHthr"),
            NS(color_name="Dark Grey Heather", mainframe_color="DkGryHthr"),
            NS(color_name="True Navy", mainframe_color="TrueNavy")]),
        NS(style="K500", brand="Port Authority", product_status="Active", skus=[
            NS(color_name="Black", mainframe_color="Black")]),
    ]
    rows = cme.sanmar_rows(styles, {"J333"})
    assert [(r["color_name"], r["short_code"]) for r in rows] == [
        ("Dark Grey Heather", "DkGryHthr"), ("True Navy", "TrueNavy")]
    assert rows[0]["source"] == "SanMar" and rows[0]["brand"] == "Port Authority"


def test_ss_rows_carry_the_colour_code():
    products = [
        {"style_name": "3000", "brand_name": "Next Level",
         "color_name": "Military Green", "color_code": "47"},
        {"style_name": "3000", "brand_name": "Next Level",
         "color_name": "Military Green", "color_code": "47"},
        {"style_name": "64000", "brand_name": "Gildan",
         "color_name": "Garnet", "color_code": "11"},
    ]
    rows = cme.ss_rows(products, {"3000"})
    assert rows == [{"source": "S&S", "style": "3000", "brand": "Next Level",
                     "color_name": "Military Green", "short_code": "47",
                     "status": ""}]

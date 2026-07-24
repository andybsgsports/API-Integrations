"""Unit test for the Momentec Weight_Unit normalizer (scripts/momentec_backfill).

Unlike SanMar/S&S (pounds only, unit inferred by native_pricing.weight_display),
the ASG feed supplies the unit directly per row -- this just normalizes its
free-text spelling to NetSuite's enum string.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from momentec_backfill import UNKNOWN_WEIGHT_UNITS, _weight_unit  # noqa: E402


def test_recognizes_lb_variants():
    for raw in ("lb", "Lb", " LBS ", "pound", "Pounds"):
        assert _weight_unit(raw) == "lb"


def test_recognizes_oz_variants():
    for raw in ("oz", "OZ", "ounce", "Ounces"):
        assert _weight_unit(raw) == "oz"


def test_blank_stays_blank():
    assert _weight_unit("") == ""


def test_unrecognized_unit_left_blank_and_logged():
    UNKNOWN_WEIGHT_UNITS.clear()
    assert _weight_unit("kg") == ""
    assert "kg" in UNKNOWN_WEIGHT_UNITS

"""Unit test for the Momentec Weight_Unit normalizer (scripts/momentec_backfill).

Unlike SanMar/S&S (pounds only, unit inferred by native_pricing.weight_display),
the ASG feed supplies the unit directly per row -- this just normalizes its
free-text spelling to NetSuite's enum string.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from momentec_backfill import (  # noqa: E402
    UNKNOWN_WEIGHT_UNITS,
    _weight_unit,
    is_closeout,
)


def test_recognizes_lb_variants():
    # weightUnit is a NetSuite reference field -- {"id": ...}, not a bare name.
    for raw in ("lb", "Lb", " LBS ", "pound", "Pounds"):
        assert _weight_unit(raw) == {"id": "1"}


def test_recognizes_oz_variants():
    for raw in ("oz", "OZ", "ounce", "Ounces"):
        assert _weight_unit(raw) == {"id": "2"}


def test_blank_stays_blank():
    assert _weight_unit("") == ""


def test_unrecognized_unit_left_blank_and_logged():
    UNKNOWN_WEIGHT_UNITS.clear()
    assert _weight_unit("kg") == ""
    assert "kg" in UNKNOWN_WEIGHT_UNITS


def test_closeout_from_ribbon():
    assert is_closeout("Closeout")
    assert is_closeout("CLOSEOUT")
    assert not is_closeout("")
    assert not is_closeout("New")
    assert not is_closeout(None)  # type: ignore[arg-type]

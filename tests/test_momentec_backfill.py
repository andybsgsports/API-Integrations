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


def test_weight_lb_passes_pounds_through():
    from momentec_backfill import _weight_lb
    assert _weight_lb("1.2", "lb") == (1.2, {"id": "1"})


def test_weight_lb_converts_ounces_to_pounds():
    # Catalogue-wide decision: shipping weight is always pounds.
    from momentec_backfill import _weight_lb
    assert _weight_lb("8", "oz") == (0.5, {"id": "1"})


def test_weight_lb_unknown_unit_writes_number_only():
    from momentec_backfill import _weight_lb
    UNKNOWN_WEIGHT_UNITS.clear()
    assert _weight_lb("2.5", "kg") == (2.5, "")
    assert "kg" in UNKNOWN_WEIGHT_UNITS


def test_closeout_from_ribbon():
    assert is_closeout("Closeout")
    assert is_closeout("CLOSEOUT")
    assert not is_closeout("")
    assert not is_closeout("New")
    assert not is_closeout(None)  # type: ignore[arg-type]


def test_same_compares_checkbox_bool_to_netsuite_tf():
    """A Python bool payload vs SuiteQL's "T"/"F" must not always differ --
    that mismatch made every run rewrite every matched item just to re-send
    an identical checkbox."""
    from momentec_backfill import _same
    assert _same("T", True)
    assert _same("F", False)
    assert _same("", False)      # never-set checkbox == unchecked
    assert not _same("F", True)
    assert not _same("T", False)

"""Unit test for the location sweep helper (scripts/item_location_fix)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from item_location_fix import location_body  # noqa: E402

LOC = "5"


def test_sets_both_when_blank():
    assert location_body({"location": "", "preferredlocation": ""}, LOC, True) == {
        "location": {"id": LOC},
        "preferredLocation": {"id": LOC},
    }


def test_only_fills_the_field_that_is_wrong():
    # Warehouse already correct, Preferred blank -> only Preferred is written.
    assert location_body({"location": LOC, "preferredlocation": ""}, LOC, True) == {
        "preferredLocation": {"id": LOC}
    }
    # both correct -> nothing to do
    assert location_body({"location": LOC, "preferredlocation": LOC}, LOC, True) == {}


def test_preferred_set_unconditionally_when_column_absent():
    assert location_body({"location": LOC}, LOC, has_pref=False) == {
        "preferredLocation": {"id": LOC}
    }

"""Unit tests for the SanMar child-finalize field logic (scripts/sanmar_child_finalize).

The live pass needs NetSuite, but the per-child body decision is pure:
* copy Department/Class/descriptions from the parent only where the child is blank;
* set Location to the fixed Badger Sporting Goods id whenever it isn't already.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sanmar_child_finalize import COPY_FIELDS, plan_body  # noqa: E402

LOC = "7"  # pretend Badger Sporting Goods location internal id


def _body(child, parent, *, location_id=None, has_location_col=True):
    return plan_body(
        child, parent, copy_cols=COPY_FIELDS,
        location_id=location_id, has_location_col=has_location_col,
    )


def test_copies_blank_fields_from_parent():
    parent = {
        "department": "12", "class": "44",       # reference cols return ids
        "description": "Soft ringspun tee",
        "purchasedescription": "Gildan Softstyle Tee",
    }
    child = {"department": "", "class": None, "description": "", "purchasedescription": ""}

    body = _body(child, parent)

    assert body == {
        "department": {"id": "12"},
        "class": {"id": "44"},
        "salesDescription": "Soft ringspun tee",
        "purchaseDescription": "Gildan Softstyle Tee",
    }


def test_never_overwrites_a_value_the_child_already_has():
    parent = {
        "department": "12", "class": "44",
        "description": "P", "purchasedescription": "P",
    }
    child = {
        "department": "99", "class": "44",
        "description": "kept", "purchasedescription": "",
    }

    body = _body(child, parent)

    assert body == {"purchaseDescription": "P"}  # only the blank one is filled


def test_location_set_when_missing_or_wrong_and_skipped_when_correct():
    parent = {"department": "12"}
    both = {"location": {"id": LOC}, "preferredLocation": {"id": LOC}}
    # blank location -> set Warehouse + Preferred Location
    assert _body({"department": "12", "location": ""}, parent, location_id=LOC) == both
    # different location -> overwrite both to Badger Sporting Goods
    assert _body({"department": "12", "location": "3"}, parent, location_id=LOC) == both
    # already correct -> no location write, nothing to do
    assert _body({"department": "12", "location": LOC}, parent, location_id=LOC) == {}


def test_location_set_unconditionally_when_column_unreadable():
    body = _body({"department": "12"}, {}, location_id=LOC, has_location_col=False)
    assert body == {"location": {"id": LOC}, "preferredLocation": {"id": LOC}}

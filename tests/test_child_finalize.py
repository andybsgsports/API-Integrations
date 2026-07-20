"""Unit tests for the SanMar child-finalize copy logic (scripts/sanmar_child_finalize).

The live pass needs NetSuite, but the parent->child copy decision is pure: copy a
field only when the parent has a value and the child is blank (never overwrite).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sanmar_child_finalize import plan_body  # noqa: E402


def test_copies_blank_fields_from_parent():
    parent = {
        "department": "12",          # reference cols return internal ids
        "class": "44",
        "salesdescription": "Soft ringspun tee",
        "purchasedescription": "Gildan Softstyle Tee",
    }
    child = {"department": "", "class": None, "salesdescription": "", "purchasedescription": ""}

    body = plan_body(child, parent)

    assert body == {
        "department": {"id": "12"},          # reference -> {id}
        "class": {"id": "44"},
        "salesDescription": "Soft ringspun tee",   # plain string
        "purchaseDescription": "Gildan Softstyle Tee",
    }


def test_never_overwrites_a_value_the_child_already_has():
    parent = {
        "department": "12", "class": "44",
        "salesdescription": "P", "purchasedescription": "P",
    }
    child = {
        "department": "99", "class": "44",
        "salesdescription": "kept", "purchasedescription": "",
    }

    body = plan_body(child, parent)

    # department/class/salesdescription already set on child -> left alone;
    # only the blank purchasedescription is filled.
    assert body == {"purchaseDescription": "P"}


def test_skips_field_when_parent_is_blank_too():
    parent = {"department": "", "class": "44", "salesdescription": "", "purchasedescription": ""}
    child = {"department": "", "class": "", "salesdescription": "", "purchasedescription": ""}

    body = plan_body(child, parent)

    assert body == {"class": {"id": "44"}}  # only the field the parent actually has

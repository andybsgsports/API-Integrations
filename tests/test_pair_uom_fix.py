"""Unit tests for the bottoms->Pair UOM fix (scripts/item_pair_uom_fix).

Detection is by plural garment word in the Display Name / item name; the fix
only flips a matched bottom whose Units Type isn't already Pair.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from item_pair_uom_fix import PAIR_UNITS_TYPE, is_bottom, plan_body  # noqa: E402


def test_detects_bottoms_across_vendors():
    assert is_bottom("Ladies B-Core Shorts")
    assert is_bottom("Russell Integrated Football Pants")
    assert is_bottom("Ladies Leggings")
    assert is_bottom("", "5108-Black-Large Joggers")  # falls back to name/code


def test_plural_avoids_short_sleeve_false_positive():
    assert not is_bottom("B-Core Short Sleeve Tee")
    assert not is_bottom("Long Sleeve Hoodie")
    assert not is_bottom("Performance Polo")


def test_sets_pair_when_bottom_and_not_already_pair():
    row = {"displayname": "B-Core Shorts", "itemid": "411600-Black-Small",
           "unitstype": "1"}  # currently Each
    body = plan_body(row)
    assert body["unitsType"] == {"id": "6"}
    assert body["stockUnit"] == {"id": "13"}
    assert body["purchaseUnit"] == {"id": "13"}
    assert body["saleUnit"] == {"id": "13"}


def test_noop_when_already_pair():
    row = {"displayname": "B-Core Shorts", "itemid": "x", "unitstype": PAIR_UNITS_TYPE}
    assert plan_body(row) == {}


def test_noop_for_non_bottom():
    row = {"displayname": "B-Core Long Sleeve Hoodie", "itemid": "410500-Silver-Small",
           "unitstype": "1"}
    assert plan_body(row) == {}

"""Unit test for the UOM body builder (scripts/item_uom_fix).

Store Description is owned by the SanMar field update now, so this builder is
UOM-only.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from item_uom_fix import build_body  # noqa: E402

UOM = {
    "unitsType": {"id": "1"}, "stockUnit": {"id": "2"},
    "purchaseUnit": {"id": "2"}, "saleUnit": {"id": "2"}, "weightUnit": "lb",
}


def test_sets_uom_when_blank():
    body = build_body({"unitstype": ""}, UOM, has_uom_col=True)
    assert body == UOM


def test_skips_uom_when_already_set():
    body = build_body({"unitstype": "1"}, UOM, has_uom_col=True)
    assert body == {}


def test_sets_uom_when_column_absent():
    # No unitstype column projects at all -> set UOM unconditionally.
    body = build_body({}, UOM, has_uom_col=False)
    assert body == UOM

"""Unit test for the UOM/store-description body builder (scripts/item_uom_fix)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from item_uom_fix import build_body  # noqa: E402

UOM = {
    "unitsType": {"id": "1"}, "stockUnit": {"id": "2"},
    "purchaseUnit": {"id": "2"}, "saleUnit": {"id": "2"}, "weightUnit": "lb",
}


def test_sets_uom_when_blank_and_store_from_sales():
    body = build_body(
        {"unitstype": "", "salesdescription": "Soft tee", "storedescription": ""},
        UOM, has_uom_col=True,
    )
    assert body == {**UOM, "storeDescription": "Soft tee"}


def test_skips_uom_when_already_set():
    body = build_body(
        {"unitstype": "1", "salesdescription": "Soft tee", "storedescription": "Soft tee"},
        UOM, has_uom_col=True,
    )
    assert body == {}  # UOM present, store already matches -> nothing


def test_store_only_when_uom_present_but_store_stale():
    body = build_body(
        {"unitstype": "1", "salesdescription": "New copy", "storedescription": "old"},
        UOM, has_uom_col=True,
    )
    assert body == {"storeDescription": "New copy"}

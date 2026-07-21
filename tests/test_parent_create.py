"""Unit test for the net-new child payload (scripts/sanmar_parent_create).

The create loop needs live NetSuite, but the per-SKU payload sent to the matrix
RESTlet is pure: it must carry the matrix keys (style/color/size), the unique
name + external id, and — critically — the UPC, so the nightly Field Update can
match the new child by upccode and enrich it.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sanmar_parent_create import _child_payload  # noqa: E402


def _style():
    return SimpleNamespace(
        style="PC90", title="Port & Company Essential Fleece Crewneck. PC90",
        description="A sturdy fleece.", category="Sweatshirts/Fleece",
    )


def _sku():
    return SimpleNamespace(
        unique_key="ABC123", color_name="Jet Black", size="XL",
        gtin="00845235100010", piece_price=Decimal("9.42"), msrp=Decimal("21.99"),
        description="",
    )


def test_child_payload_carries_matrix_keys_and_upc():
    p = _child_payload(_style(), _sku())
    assert p["externalId"] == "SANMAR-ABC123"
    assert p["itemId"] == "PC90-Jet Black-X-Large"   # size spelled out
    assert p["style"] == "PC90"
    assert p["color"] == "Jet Black"
    assert p["size"] == "X-Large"
    assert p["upc"] == "00845235100010"              # so Field Update can match it
    assert p["cost"] == 9.42
    assert p["basePrice"] == 21.99
    assert p["vendorName"] == "PC90"
    assert p["class"] == "Tops : Sweatshirts"        # mapped from category


def test_child_payload_falls_back_to_style_description():
    p = _child_payload(_style(), _sku())
    assert p["description"] == "A sturdy fleece."      # sku desc empty -> style desc

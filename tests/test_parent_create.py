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


def _sku(**over):
    """A feed SKU carrying every field the payload reads (mirrors SkuRecord)."""
    base = dict(
        unique_key="ABC123", color_name="Jet Black", size="XL",
        gtin="00845235100010", piece_price=Decimal("9.42"),
        case_price=Decimal("8.51"), msrp=Decimal("21.99"),
        map_price=Decimal("23.99"), piece_weight=Decimal("1.4"),
        description="",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_child_payload_carries_matrix_keys_and_upc():
    p = _child_payload(_style(), _sku())
    assert p["externalId"] == "SANMAR-ABC123"
    assert p["itemId"] == "PC90-Jet Black-X-Large"   # size spelled out
    assert p["style"] == "PC90"
    assert p["color"] == "Jet Black"
    assert p["size"] == "X-Large"
    assert p["upc"] == "00845235100010"              # so Field Update can match it
    assert p["vendorName"] == "PC90"


def test_child_payload_sends_no_class():
    # The RESTlet's "Parent : Child" class search crashes on this account
    # ("invalid search criteria: parent"), killing the whole child create --
    # the childless-parent bug from the live pilot (runs 30954100322 /
    # 31036109523). Class goes on the PARENT; child_finalize copies it down.
    assert "class" not in _child_payload(_style(), _sku())


class _CanonResolver:
    """Resolver stub: knows the list spells it 'Jet.Black' / 'X-Large'."""

    def canonical_name(self, list_type, name):
        return {"Jet Black": "Jet.Black"}.get(name, name)


def test_child_payload_uses_the_lists_spelling_of_options():
    # The RESTlet resolves colour/size by exact name, so a punctuation variant
    # must be sent as the list's spelling ('Khaki/ Coffee' was rejected while
    # 'Khaki/Coffee' sat on the list -- run 31036109523).
    p = _child_payload(_style(), _sku(), _CanonResolver())
    assert p["color"] == "Jet.Black"
    assert p["itemId"] == "PC90-Jet.Black-X-Large"   # name built from canonical


# --- native pricing at birth must match the nightly update's rules, or every
# created item is wrong until a later pass corrects it.

def test_cost_at_birth_is_the_case_price():
    # NOT the single-piece price -- that runs ~$1 higher and is exactly what
    # made Purchase Price read too high.
    assert _child_payload(_style(), _sku())["cost"] == 8.51


def test_cost_falls_back_to_piece_price_without_case_data():
    p = _child_payload(_style(), _sku(case_price=None))
    assert p["cost"] == 9.42


def test_base_price_at_birth_is_the_higher_of_map_and_msrp():
    # MAP 23.99 > MSRP 21.99 -> MAP wins (the old code wrote MSRP blindly).
    assert _child_payload(_style(), _sku())["basePrice"] == 23.99


def test_base_price_uses_msrp_when_no_map():
    # Value brands like Gildan carry no MAP at all.
    p = _child_payload(_style(), _sku(map_price=None))
    assert p["basePrice"] == 21.99


def test_weight_is_written_at_birth():
    assert _child_payload(_style(), _sku())["weight"] == 1.4


def test_missing_weight_is_left_unset_not_zeroed():
    assert _child_payload(_style(), _sku(piece_weight=None))["weight"] is None


def test_child_payload_falls_back_to_style_description():
    p = _child_payload(_style(), _sku())
    assert p["description"] == "A sturdy fleece."      # sku desc empty -> style desc

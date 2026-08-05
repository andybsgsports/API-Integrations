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


def _style(**over):
    base = dict(
        style="PC90", title="Port & Company Essential Fleece Crewneck. PC90",
        description="A sturdy fleece.", category="Sweatshirts/Fleece",
        images_by_color={},
    )
    base.update(over)
    return SimpleNamespace(**base)


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


def test_display_name_keeps_the_style_code_descriptions_drop_it():
    # Andy's spec (2026-08-05 pilot review): Display Name keeps the style code
    # ("... Crewneck PC90"); Sales/Purchase Description carry the same title
    # WITHOUT it -- the marketing copy belongs to the PARENT's Store
    # Description instead.
    p = _child_payload(_style(), _sku())
    assert p["displayName"] == "Port & Company Essential Fleece Crewneck PC90"
    assert p["description"] == "Port & Company Essential Fleece Crewneck"


def test_child_payload_carries_weight_unit_with_weight():
    p = _child_payload(_style(), _sku())
    assert p["weight"] == 1.4
    assert p["weightUnitId"] == "1"                    # pounds, account-verified
    q = _child_payload(_style(), _sku(piece_weight=None))
    assert q["weight"] is None and q["weightUnitId"] is None


def test_child_payload_seeds_sanmar_as_preferred_vendor():
    assert _child_payload(_style(), _sku())["preferredVendorId"] == "512"


def test_child_payload_carries_per_colour_images():
    imgs = SimpleNamespace(primary_url=lambda: "https://cdn/front.jpg",
                           back_url=lambda: "https://cdn/back.jpg",
                           front_flat_url="https://cdn/front_flat.jpg",
                           back_flat_url="https://cdn/back_flat.jpg",
                           color_swatch_url="https://cdn/swatch.jpg")
    p = _child_payload(_style(images_by_color={"Jet Black": imgs}), _sku())
    assert p["shopImageUrl"] == "https://cdn/front.jpg"   # storefront column
    assert p["backImageUrl"] == "https://cdn/back.jpg"    # custitem_sanmar_front_image_url
    assert p["frontFlatUrl"] == "https://cdn/front_flat.jpg"
    assert p["backFlatUrl"] == "https://cdn/back_flat.jpg"
    assert p["swatchUrl"] == "https://cdn/swatch.jpg"
    q = _child_payload(_style(), _sku())                  # colour has no images
    assert q["shopImageUrl"] is None and q["backImageUrl"] is None
    assert q["frontFlatUrl"] is None and q["swatchUrl"] is None


def test_child_payload_carries_uom_ids_when_resolved():
    uom = {"unitsTypeId": "3", "stockUnitId": "7", "purchaseUnitId": "7",
           "saleUnitId": "7"}
    p = _child_payload(_style(), _sku(), None, uom)
    for k, v in uom.items():
        assert p[k] == v

"""Unit tests for the matrix value classifier (scripts/sanmar_ensure_matrix_values).

The classify step decides, for each colour/size the create-import couldn't
resolve, whether it's a genuinely new value (create it) or a punctuation/spacing
variant of one you already have (remap, don't duplicate).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sanmar_ensure_matrix_values import _norm, classify  # noqa: E402


def test_norm_ignores_spacing_and_punctuation():
    assert _norm("J. Navy") == _norm("J.Navy") == "jnavy"
    assert _norm("Anthracite Heather/ Black") == _norm("Anthracite Heather/Black")


def test_variant_maps_to_existing_new_is_created():
    existing = {_norm("J.Navy"): "J.Navy", _norm("Forest Green"): "Forest Green"}
    new, variants = classify(["J. Navy", "Apricot", "forest  green"], existing)

    assert new == ["Apricot"]                     # genuinely new
    assert variants == {                          # spacing/case variants -> canonical
        "J. Navy": "J.Navy",
        "forest  green": "Forest Green",
    }


def test_exact_existing_name_is_neither_new_nor_variant():
    existing = {_norm("Black"): "Black"}
    new, variants = classify(["Black"], existing)
    assert new == []          # already exists exactly -> nothing to do
    assert variants == {}

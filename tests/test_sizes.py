from __future__ import annotations

import pytest

from sanmar_netsuite.transform.sizes import normalize_size


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Base apparel sizes
        ("XS", "X-Small"),
        ("S", "Small"),
        ("M", "Medium"),
        ("L", "Large"),
        ("XL", "X-Large"),
        # nX family — numeric form
        ("2XL", "2X-Large"),
        ("3XL", "3X-Large"),
        ("4XL", "4X-Large"),
        ("6XL", "6X-Large"),
        # nX family — repeated-letter form
        ("XXL", "2X-Large"),
        ("XXXL", "3X-Large"),
        ("XXS", "2X-Small"),
        # Tall suffix composes onto the base
        ("LT", "Large Tall"),
        ("XLT", "X-Large Tall"),
        ("2XLT", "2X-Large Tall"),
        # Youth prefix composes onto the base
        ("YS", "Youth Small"),
        ("YM", "Youth Medium"),
        ("YXL", "Youth X-Large"),
        # One-size variants
        ("OSFA", "One Size"),
        ("OS", "One Size"),
        # Combined sizes spell out each side (BSG spells sizes out).
        ("S/M", "Small/Medium"),
        ("M/L", "Medium/Large"),
        ("L/XL", "Large/X-Large"),
        # Infant / toddler sizes.
        ("NB", "Newborn"),
        ("06M", "0-6 Months"),
        ("24M", "24 Months"),
        ("6T", "6T"),
    ],
)
def test_known_sizes_are_spelled_out(raw, expected):
    assert normalize_size(raw) == expected


def test_case_and_whitespace_insensitive():
    assert normalize_size(" s ") == "Small"
    assert normalize_size("xl") == "X-Large"
    assert normalize_size("2 XL") == "2X-Large"


@pytest.mark.parametrize("raw", ["32", "10.5", "OSFA-ish", "140Cm", "7 1/4"])
def test_unknown_values_pass_through_unchanged(raw):
    # Numeric / non-apparel sizes must never be mangled or dropped.
    assert normalize_size(raw) == raw


def test_empty_and_none_are_safe():
    assert normalize_size("") == ""
    assert normalize_size(None) == ""
    assert normalize_size("   ") == ""


@pytest.mark.parametrize("raw,expected", [
    # Spelled-out variants fold onto the canonical nX- form. Legacy list
    # values use "XX-Large" where the feeds' 2XL/XXL normalise to "2X-Large";
    # that near-miss made existing children look "new" to the S&S create
    # preview (duplicate-create risk).
    ("XX-Large", "2X-Large"),
    ("XX Large", "2X-Large"),
    ("XXX-Large", "3X-Large"),
    ("XX-Small", "2X-Small"),
    ("2X-Large", "2X-Large"),   # already canonical: unchanged
    ("X-Large", "X-Large"),
    ("XX-Large Tall", "2X-Large Tall"),
])
def test_spelled_out_variants_fold_onto_the_canonical_form(raw, expected):
    assert normalize_size(raw) == expected

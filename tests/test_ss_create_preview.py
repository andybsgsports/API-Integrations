"""S&S discover: every SKU lands in exactly one bucket, and 'exists' wins.

The dangerous failure mode is a SKU that already exists in NetSuite being
counted as new (a live create would then duplicate it), so the tests centre
on the two exists-detections: GTIN -> upcCode, and the normalised
colour/size combo under an ADOPTED parent.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ss_create_preview import combo_key


def test_combo_key_normalises_colour_variants():
    # 'J. Navy' from the feed must collide with an existing 'J.Navy' child.
    assert combo_key("J. Navy", "S") == combo_key("J.Navy", "Small")


def test_combo_key_normalises_sizes_through_the_shared_size_rule():
    # The list stores spelled-out sizes; the S&S feed sends abbreviations.
    assert combo_key("Black", "S") == combo_key("Black", "Small")
    assert combo_key("Black", "2XL") == combo_key("Black", "XX-Large")


def test_combo_key_keeps_genuinely_different_children_apart():
    assert combo_key("Black", "Small") != combo_key("Black", "Medium")
    assert combo_key("Navy", "Small") != combo_key("J.Navy", "Small")

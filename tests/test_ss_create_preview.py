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


def test_combo_key_folds_the_two_spellings_of_the_X_family():
    # A child stored as 'XX-Large' must collide with the feed's '2XL', or the
    # preview counts an existing child as new and queues a duplicate create.
    assert combo_key("Black", "2XL") == combo_key("Black", "XX-Large")
    assert combo_key("Black", "XXL") == combo_key("Black", "XX-Large")
    assert combo_key("Black", "3XL") == combo_key("Black", "XXX-Large")
    assert combo_key("Black", "XS") == combo_key("Black", "X-Small")


def test_the_fold_does_not_touch_the_shared_size_rule():
    # Folding lives in the preview because normalize_size's output is matched
    # against live list-value names by exact string; changing it would alter
    # every vendor's update path (OptionMaps.size_candidates).
    from sanmar_netsuite.transform.sizes import normalize_size
    assert normalize_size("XX-Large") == "XX-Large"
    assert normalize_size("2XL") == "2X-Large"


def test_single_x_sizes_are_not_folded_into_a_number():
    assert combo_key("Black", "XL") != combo_key("Black", "2XL")
    assert combo_key("Black", "XL") == combo_key("Black", "X-Large")


def test_combo_key_keeps_genuinely_different_children_apart():
    assert combo_key("Black", "Small") != combo_key("Black", "Medium")
    assert combo_key("Navy", "Small") != combo_key("J.Navy", "Small")

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

from collections import Counter

from ss_create_preview import (
    brand_allowed,
    brand_allowlist,
    combo_key,
    excluded_brands,
)


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


# --- brand allowlist: creation is scoped to brands BSG actually sells

def test_named_brands_match_ignoring_punctuation_and_case(monkeypatch):
    monkeypatch.setenv("SS_CREATE_BRANDS", "Bella+Canvas, Gildan")
    allowed = brand_allowlist()
    assert brand_allowed("BELLA + CANVAS", allowed)
    assert brand_allowed("gildan", allowed)
    assert not brand_allowed("Anvil", allowed)


def test_an_empty_allowlist_permits_every_brand(monkeypatch):
    monkeypatch.delenv("SS_CREATE_BRANDS", raising=False)
    allowed = brand_allowlist()
    assert allowed == set()
    assert brand_allowed("anything at all", allowed)


def test_CARRIED_resolves_to_the_brands_already_stocked(monkeypatch):
    # Andy chose "all 36 brands we carry today" -- a rule, not a frozen list,
    # so it resolves from the live catalogue each run.
    monkeypatch.setenv("SS_CREATE_BRANDS", "CARRIED")
    carried = Counter({"Gildan": 7952, "BELLA + CANVAS": 6919, "Augusta": 0})
    allowed = brand_allowlist(carried)
    assert brand_allowed("gildan", allowed)
    assert brand_allowed("Bella+Canvas", allowed)
    assert not brand_allowed("Augusta", allowed)      # stocked zero today
    assert not brand_allowed("Never Heard Of It", allowed)


def test_CARRIED_can_be_combined_with_explicitly_named_brands(monkeypatch):
    monkeypatch.setenv("SS_CREATE_BRANDS", "CARRIED, Augusta Sportswear")
    allowed = brand_allowlist(Counter({"Gildan": 10}))
    assert brand_allowed("Gildan", allowed)
    assert brand_allowed("augusta sportswear", allowed)


# --- brands bought direct from Momentec must never be created from S&S

def test_momentec_brands_are_excluded_even_when_carried(monkeypatch):
    # The trap in the CARRIED rule: Badger/Augusta/etc show a small carried
    # count because those items came from the MOMENTEC feed, and S&S then
    # offers the whole catalogue behind them (~42,700 SKUs across six brands).
    monkeypatch.setenv("SS_CREATE_BRANDS", "CARRIED")
    monkeypatch.delenv("SS_EXCLUDE_BRANDS", raising=False)
    carried = Counter({"Badger": 231, "Augusta Sportswear": 146,
                       "Holloway": 98, "Gildan": 7952})
    allowed, excluded = brand_allowlist(carried), excluded_brands()
    assert brand_allowed("Gildan", allowed, excluded)
    for direct in ("Badger", "Augusta Sportswear", "Holloway",
                   "Alleson Athletic", "Russell Athletic", "C2 Sport",
                   "High Five", "Pacific Headwear"):
        assert not brand_allowed(direct, allowed, excluded), direct


def test_every_momentec_supplied_brand_is_covered():
    # momentec_backfill.BRAND_NAMES is the source of truth for who Momentec
    # supplies; adding a brand code there must not silently leave that brand
    # exposed to S&S creation. S&S spells some differently ('C2 Sport').
    from momentec_backfill import BRAND_NAMES
    from ss_create_preview import MOMENTEC_DIRECT_BRANDS, _brand_key
    covered = {_brand_key(b) for b in MOMENTEC_DIRECT_BRANDS}
    missing = sorted({b for b in BRAND_NAMES.values()
                      if _brand_key(b) not in covered})
    assert not missing, f"Momentec-supplied brand(s) not excluded: {missing}"


def test_the_exclusion_can_be_overridden(monkeypatch):
    monkeypatch.setenv("SS_CREATE_BRANDS", "CARRIED")
    monkeypatch.setenv("SS_EXCLUDE_BRANDS", "")
    allowed, excluded = brand_allowlist(Counter({"Badger": 231})), excluded_brands()
    assert excluded == set()
    assert brand_allowed("Badger", allowed, excluded)

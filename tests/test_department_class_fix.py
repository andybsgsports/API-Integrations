"""Unit tests for the SanMar Department/Class correction body builder
(scripts/sanmar_department_class_fix).

Unlike child-finalize's fill-blanks-only copy, this CORRECTS an item's
Department/Class whenever the computed-correct value differs from what's
currently set -- it doesn't just fill in blanks.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sanmar_department_class_fix import plan_body  # noqa: E402

DEPT_ID = "9"  # pretend "Apparel" department internal id
TEES_ID = "44"  # pretend "Tops : Tees" class internal id


def test_sets_both_when_blank():
    row = {"department": "", "class": ""}
    body = plan_body(row, correct_class="Tops : Tees", dept_id=DEPT_ID, class_id=TEES_ID)
    assert body == {"department": {"id": DEPT_ID}, "class": {"id": TEES_ID}}


def test_corrects_wrong_class_not_just_blank():
    # class is SET but to the wrong id -- this must still be corrected.
    row = {"department": DEPT_ID, "class": "999"}
    body = plan_body(row, correct_class="Tops : Tees", dept_id=DEPT_ID, class_id=TEES_ID)
    assert body == {"class": {"id": TEES_ID}}


def test_unchanged_when_already_correct():
    row = {"department": DEPT_ID, "class": TEES_ID}
    body = plan_body(row, correct_class="Tops : Tees", dept_id=DEPT_ID, class_id=TEES_ID)
    assert body == {}


def test_class_left_alone_when_unresolved():
    # correct_class computed, but resolve_class_ids never found that name in
    # NetSuite (class_id is None) -- don't guess, leave the field alone.
    row = {"department": "", "class": "999"}
    body = plan_body(row, correct_class="Bottoms : Pants", dept_id=DEPT_ID, class_id=None)
    assert body == {"department": {"id": DEPT_ID}}


def test_no_department_id_leaves_department_alone():
    row = {"department": "", "class": ""}
    body = plan_body(row, correct_class="Tops : Tees", dept_id=None, class_id=TEES_ID)
    assert body == {"class": {"id": TEES_ID}}

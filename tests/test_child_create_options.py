"""Children under EXISTING parents: new colours get created, name clashes skip.

Andy, 2026-11-12, after run 43213713852 failed on 6 'Athletic Grey' children
(colour not on the list) and 4 "Uniqueness error" children: add missing
colours automatically, and treat "name already exists" as a skip.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest  # noqa: E402
from sanmar_child_create import (  # noqa: E402
    RetiredOption,
    ensure_options,
    is_name_taken,
    is_option_not_on_list,
)

from sanmar_netsuite.netsuite.matrix_options import COLOR_LIST, SIZE_LIST  # noqa: E402


class _Resolver:
    def __init__(self, known, allow_create=True, retired=()):
        self.known = set(known)
        self.retired = set(retired)
        self.allow_create = allow_create
        self.calls = []

    def is_retired(self, list_type, name):
        return (list_type, name) in self.retired

    def resolve(self, list_type, name):
        self.calls.append((list_type, name))
        if (list_type, name) in self.known:
            return "1", "existing"
        if not self.allow_create:
            return None, "missing"
        self.known.add((list_type, name))
        return "2", "created"


def _sku(color="Athletic Grey", size="L"):
    return SimpleNamespace(color_name=color, size=size)


def test_missing_colour_is_created_and_reported():
    r = _Resolver({(SIZE_LIST, "Large")})
    new = ensure_options(r, _sku())
    assert new == ["colour 'Athletic Grey'"]
    assert (COLOR_LIST, "Athletic Grey") in r.known


def test_known_colour_and_size_report_nothing():
    r = _Resolver({(COLOR_LIST, "Athletic Grey"), (SIZE_LIST, "Large")})
    assert ensure_options(r, _sku()) == []


def test_dry_run_reports_without_creating():
    r = _Resolver({(SIZE_LIST, "Large")}, allow_create=False)
    assert ensure_options(r, _sku()) == ["colour 'Athletic Grey'"]
    assert (COLOR_LIST, "Athletic Grey") not in r.known


def test_name_taken_only_matches_the_uniqueness_error():
    assert is_name_taken(
        "Uniqueness error - there is already an item with that name or "
        "name/parent combination."
    )
    assert not is_name_taken("color 'Oat' not in customlist_bsg_matrix_color")
    assert not is_name_taken(None)


def test_retired_colour_is_a_skip_and_nothing_is_created():
    r = _Resolver({(SIZE_LIST, "Large")}, retired={(COLOR_LIST, "Athletic Grey")})
    with pytest.raises(RetiredOption, match="Athletic Grey"):
        ensure_options(r, _sku())
    assert r.calls == []


def test_not_on_list_message_is_recognised():
    assert is_option_not_on_list(
        "color 'Athletic Grey' not in customlist_bsg_matrix_color")
    assert not is_option_not_on_list("Uniqueness error - there is already")

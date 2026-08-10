"""Option-value identity: a punctuation variant must never become a duplicate.

Caught by the 2026-08-04 SanMar pipeline dry run (run 30947041147). The
ensure-values pre-pass normalises punctuation away and so correctly refused to
create 'J. Navy' as a duplicate of the existing 'J.Navy' -- but the create
path's MatrixOptionResolver matched only on exact (case-insensitive) name,
missed it, and with allow_create on would have created the duplicate. Hundreds
of colours were queued to be duplicated that way; same failure mode as the
Forest/Forrest mess.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sanmar_netsuite.netsuite.matrix_options import (
    COLOR_LIST,
    MatrixOptionResolver,
    normalize_option_name,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


class _Client:
    """Stands in for NetSuite: an option list plus a record of what was created."""

    def __init__(self, rows):
        self.rows = rows
        self.created: list[dict] = []
        self.queries: list[str] = []

    def suiteql(self, q):
        self.queries.append(q)
        if "LOWER(name) = LOWER(" in q:                    # exact-name lookup
            wanted = q.split("LOWER('")[1].split("')")[0].lower()
            return [r for r in self.rows
                    if str(r["name"]).lower() == wanted]
        return list(self.rows)                            # whole-list scan

    def create_record(self, list_type, body):
        self.created.append(body)
        return f"new-{len(self.created)}"


LIST = [
    {"id": 10, "name": "J.Navy", "isinactive": "F"},
    {"id": 11, "name": "Black/Red", "isinactive": "F"},
    {"id": 12, "name": "ASH GREY", "isinactive": "F"},
]


# --- the shared identity rule

@pytest.mark.parametrize("a,b", [
    ("J. Navy", "J.Navy"),
    ("Black/ Red", "Black/Red"),
    ("Ash Grey", "ASH GREY"),
    ("Anthracite Heather/ Black", "Anthracite Heather/Black"),
])
def test_punctuation_and_case_variants_share_a_key(a, b):
    assert normalize_option_name(a) == normalize_option_name(b)


def test_genuinely_different_colours_do_not_collide():
    assert normalize_option_name("Navy") != normalize_option_name("J.Navy")
    assert normalize_option_name("Pastel Blue") != normalize_option_name("Pastel Mint")


def test_ensure_values_shares_the_resolver_definition():
    # Same function object, so the two paths cannot drift apart again.
    from sanmar_ensure_matrix_values import _norm
    assert _norm is normalize_option_name


# --- the resolver must reuse, not duplicate

def test_variant_resolves_to_the_existing_value_instead_of_creating():
    client = _Client(LIST)
    r = MatrixOptionResolver(client=client, allow_create=True)
    rid, status = r.resolve(COLOR_LIST, "J. Navy")     # feed spelling
    assert (rid, status) == ("10", "existing")         # the canonical 'J.Navy'
    assert client.created == []                        # nothing duplicated


def test_exact_match_still_wins_directly():
    client = _Client(LIST)
    r = MatrixOptionResolver(client=client, allow_create=True)
    assert r.resolve(COLOR_LIST, "J.Navy") == ("10", "existing")
    assert client.created == []


def test_a_genuinely_new_colour_is_still_created():
    client = _Client(LIST)
    r = MatrixOptionResolver(client=client, allow_create=True)
    rid, status = r.resolve(COLOR_LIST, "Signal Red")
    assert status == "created"
    assert client.created == [{"name": "Signal Red"}]


def test_variant_is_not_reported_missing_when_creation_is_disabled():
    # Dry runs print what they WOULD create; a variant must not appear there.
    client = _Client(LIST)
    r = MatrixOptionResolver(client=client, allow_create=False)
    assert r.resolve(COLOR_LIST, "Black/ Red") == ("11", "existing")


def test_retired_duplicate_loses_to_the_active_value():
    client = _Client([
        {"id": 5, "name": "Forrest", "isinactive": "T"},   # retired misspelling
        {"id": 9, "name": "Forest", "isinactive": "F"},
    ])
    r = MatrixOptionResolver(client=client, allow_create=True)
    rid, status = r.resolve(COLOR_LIST, "forest ")
    assert (rid, status) == ("9", "existing")
    assert client.created == []


def test_whole_list_is_scanned_once_across_many_lookups():
    client = _Client(LIST)
    r = MatrixOptionResolver(client=client, allow_create=True)
    for name in ("J. Navy", "Black/ Red", "Ash Grey"):
        r.resolve(COLOR_LIST, name)
    scans = [q for q in client.queries if "LOWER(name) = LOWER(" not in q]
    assert len(scans) == 1, "the list index should be cached, not re-queried"


def test_canonical_name_prefers_the_active_value_over_a_retired_exact_match():
    # A name matching a RETIRED value exactly used to come back unchanged; the
    # RESTlet then resolved it to that retired id and NetSuite rejected the
    # child outright ("Invalid Field Value <id> for ...
    # matrixoptioncustitem_bsg_color") -- 322 children died on 2026-08-08.
    client = _Client([
        {"id": 1331, "name": "Forrest", "isinactive": "T"},   # retired
        {"id": 90, "name": "Forrest", "isinactive": "F"},     # live duplicate
    ])
    r = MatrixOptionResolver(client=client, allow_create=True)
    assert r.canonical_name(COLOR_LIST, "Forrest") == "Forrest"
    assert r.resolve(COLOR_LIST, "Forrest") == ("90", "existing")
    assert client.created == []


def test_canonical_name_maps_a_variant_onto_the_active_spelling():
    client = _Client([
        {"id": 5, "name": "J. Navy", "isinactive": "T"},      # retired spelling
        {"id": 10, "name": "J.Navy", "isinactive": "F"},      # the live one
    ])
    r = MatrixOptionResolver(client=client, allow_create=True)
    assert r.canonical_name(COLOR_LIST, "J. Navy") == "J.Navy"


def test_canonical_name_leaves_a_genuinely_new_colour_alone():
    r = MatrixOptionResolver(client=_Client(LIST), allow_create=True)
    assert r.canonical_name(COLOR_LIST, "Signal Red") == "Signal Red"

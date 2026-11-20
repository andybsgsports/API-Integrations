"""New colour values are created in BSG style (Andy, 2026-11-20)."""

from __future__ import annotations

import pytest

from sanmar_netsuite.netsuite.matrix_options import (
    COLOR_LIST,
    SIZE_LIST,
    MatrixOptionResolver,
    normalize_option_name,
    tidy_color_name,
)


@pytest.mark.parametrize(("feed", "tidy"), [
    ("Aluminum Grey/ Bright White", "Aluminum Grey/Bright White"),
    ("Anthracite / Black", "Anthracite/Black"),
    ("  True   Navy ", "True Navy"),
    ("BLACK/WHITE", "Black/White"),
    ("J. NAVY", "J. Navy"),
    ("TEAM 365 RED", "Team 365 Red"),
    ("2XL BLUE", "2XL Blue"),
    ("Navy", "Navy"),
    ("PosiCharge Red", "PosiCharge Red"),
    ("Dark Grey/CoolGrey/Volt", "Dark Grey/CoolGrey/Volt"),
    ("", ""),
])
def test_tidy(feed, tidy):
    assert tidy_color_name(feed) == tidy
    assert normalize_option_name(feed) == normalize_option_name(tidy)


class _Client:
    def __init__(self):
        self.created = []
        self.rows = [{"id": "1", "name": "Navy", "isinactive": "F"}]

    def suiteql(self, query, **_kw):
        if "LOWER(name)" in query:
            want = query.split("LOWER('")[1].split("')")[0].lower()
            return [r for r in self.rows if r["name"].lower() == want]
        return list(self.rows)

    def create_record(self, list_type, body):
        self.created.append((list_type, dict(body)))
        return str(100 + len(self.created))


def test_resolver_creates_the_tidy_name_and_keeps_answering_with_it():
    client = _Client()
    r = MatrixOptionResolver(client)
    r.canonical_name(COLOR_LIST, "Navy")  # loads the index, as a create run does
    new_id, status = r.resolve(COLOR_LIST, "Aluminum Grey/ Bright White")
    assert status == "created"
    assert client.created == [(COLOR_LIST, {"name": "Aluminum Grey/Bright White"})]
    assert r.canonical_name(COLOR_LIST, "Aluminum Grey/ Bright White") == (
        "Aluminum Grey/Bright White")
    assert r.resolve(COLOR_LIST, "Aluminum Grey/ Bright White") == (new_id, "existing")
    assert r.is_retired(COLOR_LIST, "Aluminum Grey/ Bright White") is False


def test_sizes_are_not_reworded():
    client = _Client()
    r = MatrixOptionResolver(client)
    r.resolve(SIZE_LIST, "LT/ XLT")
    assert client.created == [(SIZE_LIST, {"name": "LT/ XLT"})]

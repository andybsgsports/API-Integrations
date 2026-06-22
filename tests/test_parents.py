from __future__ import annotations

from types import SimpleNamespace

from sanmar_netsuite.netsuite.parents import numeric_styles, resolve_numeric_parent_ids


class FakeClient:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.queries: list[str] = []

    def suiteql(self, query: str, **_kw):
        self.queries.append(query)
        return self._rows


def _styles(*names: str):
    return [SimpleNamespace(style=n, skus=[]) for n in names]


def test_numeric_styles_filters_to_digit_names():
    assert numeric_styles(_styles("K420", "2000", "5000", "PC54")) == ["2000", "5000"]


def test_resolve_maps_numeric_style_to_parent_internal_id():
    client = FakeClient([{"id": "100102", "itemid": "2000"}])
    refs = resolve_numeric_parent_ids(client, _styles("K420", "2000"))
    assert refs == {"2000": "100102"}
    # alphanumeric K420 is not part of the lookup
    assert "2000" in client.queries[0] and "K420" not in client.queries[0]


def test_resolve_makes_no_query_without_numeric_styles():
    client = FakeClient([])
    assert resolve_numeric_parent_ids(client, _styles("K420")) == {}
    assert client.queries == []


def test_resolve_ignores_unrelated_rows():
    # a stray row whose itemid isn't one we asked for is dropped
    client = FakeClient([{"id": "2000", "itemid": "5422-Chht-Medium"}])
    assert resolve_numeric_parent_ids(client, _styles("2000")) == {}

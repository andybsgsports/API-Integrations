from __future__ import annotations

from types import SimpleNamespace

from sanmar_netsuite.netsuite.parents import (
    numeric_styles,
    resolve_numeric_parent_ids,
    resolve_parent_ids,
)


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


class _CaseTwinClient:
    """Old parent "St650" owns the children; the pipeline's later "ST650" is
    empty (the live sandbox state on 2026-11-11: ids 78317 and 382277)."""

    def suiteql(self, query: str, **_kw):
        if "GROUP BY parent" in query:
            return [{"parent": "78317", "n": 190}]
        return [
            {"id": "382277", "itemid": "ST650"},
            {"id": "78317", "itemid": "St650"},
            {"id": "5", "itemid": "pc54"},
        ]


def test_parent_lookup_ignores_case_and_prefers_the_parent_with_children():
    refs = resolve_parent_ids(_CaseTwinClient(), _styles("ST650", "PC54"))
    # ST650 -> the twin that actually has the 190 children, not the empty one;
    # PC54 -> found even though the catalogue spells it "pc54".
    assert refs == {"ST650": "78317", "PC54": "5"}


def test_empty_case_twins_fall_back_to_exact_spelling():
    class _NoChildren(_CaseTwinClient):
        def suiteql(self, query: str, **_kw):
            if "GROUP BY parent" in query:
                return []
            return super().suiteql(query)

    refs = resolve_parent_ids(_NoChildren(), _styles("ST650"))
    assert refs["ST650"] == "382277"

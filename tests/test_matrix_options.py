from __future__ import annotations

import re
from types import SimpleNamespace

from sanmar_netsuite.netsuite.matrix_options import (
    COLOR_LIST,
    SIZE_LIST,
    MatrixOptionResolver,
    ensure_matrix_options,
)


class FakeNS:
    """Stand-in for NetSuiteClient: matches values by name, records creates."""

    def __init__(self, existing: dict[str, dict[str, str]]) -> None:
        # existing: list_type -> {name.casefold(): internal_id}
        self.lists = {k: dict(v) for k, v in existing.items()}
        self.created: list[tuple[str, str, str]] = []  # (list_type, name, id)
        self._next = 9000

    def suiteql(self, query: str, **_kw):
        m = re.search(r"FROM (\w+) WHERE LOWER\(name\) = LOWER\('(.*)'\) ORDER BY", query)
        if m:
            list_type, name = m.group(1), m.group(2)
            idx = self.lists.get(list_type, {}).get(name.casefold())
            return [{"id": idx}] if idx is not None else []
        # Whole-list scan, used to spot punctuation variants of existing values
        # before creating a near-duplicate (see normalize_option_name).
        scan = re.search(r"FROM (\w+) ORDER BY", query)
        assert scan, f"unexpected query: {query}"
        return [
            {"id": idx, "name": name, "isinactive": "F"}
            for name, idx in self.lists.get(scan.group(1), {}).items()
        ]

    def create_record(self, list_type: str, body: dict) -> str:
        self._next += 1
        new_id = str(self._next)
        name = body["name"]
        self.lists.setdefault(list_type, {})[name.casefold()] = new_id
        self.created.append((list_type, name, new_id))
        return new_id


def _style(*skus: tuple[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        skus=[SimpleNamespace(color_name=c, size=s) for c, s in skus]
    )


EXISTING = {
    COLOR_LIST: {"black": "1", "white": "6", "navy": "531"},
    SIZE_LIST: {"small": "1", "medium": "2", "large": "3", "x-large": "4"},
}


def test_existing_values_are_reused_not_recreated():
    ns = FakeNS(EXISTING)
    styles = [_style(("Black", "S"), ("White", "M"), ("Navy", "L"))]
    report = ensure_matrix_options(ns, styles, allow_create=True)
    assert ns.created == []
    assert report.created_count == 0
    assert set(report.existing["color"]) == {"Black", "White", "Navy"}
    assert set(report.existing["size"]) == {"Small", "Medium", "Large"}


def test_missing_color_is_created():
    ns = FakeNS(EXISTING)
    report = ensure_matrix_options(ns, [_style(("Classic Navy", "S"))], allow_create=True)
    assert [name for _lt, name, _id in ns.created] == ["Classic Navy"]
    assert ns.created[0][0] == COLOR_LIST
    assert report.changed["color"] == ["Classic Navy"]
    assert report.changed["size"] == []  # 'Small' already exists


def test_dry_run_reports_missing_without_writing():
    ns = FakeNS(EXISTING)
    report = ensure_matrix_options(ns, [_style(("Classic Navy", "S"))], allow_create=False)
    assert ns.created == []  # nothing written
    assert report.changed["color"] == ["Classic Navy"]
    assert "would create" in report.summary()


def test_size_abbreviations_match_spelled_out_entries():
    ns = FakeNS(EXISTING)
    report = ensure_matrix_options(ns, [_style(("Black", "S"), ("Black", "XL"))], allow_create=True)
    # S -> Small and XL -> X-Large both already present, so nothing is created.
    assert ns.created == []
    assert set(report.existing["size"]) == {"Small", "X-Large"}


def test_match_is_case_insensitive():
    ns = FakeNS(EXISTING)
    report = ensure_matrix_options(ns, [_style(("navy", "s"))], allow_create=True)
    assert ns.created == []
    assert report.existing["color"] == ["navy"]


def test_resolver_creates_each_value_only_once():
    ns = FakeNS(EXISTING)
    resolver = MatrixOptionResolver(client=ns, allow_create=True)
    id1, status1 = resolver.resolve(COLOR_LIST, "Heather Grey")
    id2, status2 = resolver.resolve(COLOR_LIST, "heather grey")  # same name, other case
    assert id1 == id2
    assert status1 == "created"
    assert status2 == "existing"  # served from cache, not re-created
    assert len(ns.created) == 1

"""is_retired: a value that is on the list only as inactive rows."""

from __future__ import annotations

from sanmar_netsuite.netsuite.matrix_options import COLOR_LIST, MatrixOptionResolver


class _Client:
    def __init__(self, rows):
        self.rows = rows

    def suiteql(self, _q):
        return self.rows


def _resolver(rows):
    return MatrixOptionResolver(client=_Client(rows), allow_create=False)


def test_only_inactive_rows_means_retired():
    r = _resolver([{"id": 1, "name": "Athletic Grey", "isinactive": "T"}])
    assert r.is_retired(COLOR_LIST, "Athletic Grey")


def test_an_active_duplicate_means_not_retired():
    r = _resolver([
        {"id": 1, "name": "Athletic Heather", "isinactive": "T"},
        {"id": 2, "name": "Athletic Heather", "isinactive": "F"},
    ])
    assert not r.is_retired(COLOR_LIST, "Athletic Heather")


def test_a_value_not_on_the_list_is_not_retired():
    r = _resolver([{"id": 1, "name": "Navy", "isinactive": "F"}])
    assert not r.is_retired(COLOR_LIST, "Oat")
    assert not r.is_retired(COLOR_LIST, "")

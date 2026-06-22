from __future__ import annotations

from types import SimpleNamespace

from sanmar_netsuite.netsuite.merge import prepare_merge


class FakeClient:
    """Routes SuiteQL by content: parents, the color/size lists, and combos."""

    def suiteql(self, query: str, **_kw):
        if "FROM item WHERE itemid IN" in query:
            return [{"id": "50286", "itemid": "K420"}]
        if "customlist_bsg_matrix_color" in query:
            return [{"id": "1", "name": "Black"}, {"id": "7452", "name": "Classic Navy"}]
        if "customlist_bsg_matrix_size" in query:
            return [{"id": "1", "name": "Small"}]
        if "FROM item WHERE parent" in query:
            return [{"color": "1", "size": "1"}]  # Black/Small already exists
        return []


def _sku(unique_key, color, size):
    return SimpleNamespace(unique_key=unique_key, color_name=color, size=size)


def _style():
    return SimpleNamespace(
        style="K420",
        skus=[_sku("A", "Black", "S"), _sku("B", "Classic Navy", "S")],
    )


def test_prepare_merge_resolves_parent_and_skips_existing_combo():
    parent_refs, skip = prepare_merge(FakeClient(), [_style()])
    assert parent_refs == {"K420": "50286"}
    # Black/Small already exists -> skipped; Classic Navy/Small is new -> kept.
    assert skip == {"SANMAR-A"}

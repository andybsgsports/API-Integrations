from __future__ import annotations

from types import SimpleNamespace

from sanmar_netsuite.netsuite.merge import prepare_merge


class FakeClient:
    """Routes SuiteQL by content: parents, the color/size lists, and combos."""

    def suiteql(self, query: str, **_kw):
        if "FROM item WHERE UPPER(itemid) IN" in query:
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


class DuplicateColourClient:
    """The colour list holds "White" twice (retired id 6, active id 1384) and a
    punctuation variant of the feed's "Black/ Red"; existing children point at
    the ACTIVE ids -- the live PC54-White case (2026-11-11)."""

    def suiteql(self, query: str, **_kw):
        if "FROM item WHERE UPPER(itemid) IN" in query:
            return [{"id": "100", "itemid": "PC54"}]
        if "customlist_bsg_matrix_color" in query:
            # Retired duplicate listed LAST: the old one-id-per-name dict kept
            # it and missed the child.
            return [
                {"id": "1384", "name": "White"},
                {"id": "77", "name": "Black/Red"},
                {"id": "6", "name": "White"},
            ]
        if "customlist_bsg_matrix_size" in query:
            return [{"id": "1", "name": "Small"}]
        if "FROM item WHERE parent" in query:
            return [{"color": "1384", "size": "1"}, {"color": "77", "size": "1"}]
        return []


def test_existing_child_is_skipped_whichever_duplicate_colour_id_it_uses():
    style = SimpleNamespace(
        style="PC54",
        skus=[_sku("W", "White", "S"), _sku("BR", "Black/ Red", "S"),
              _sku("N", "Navy", "S")],
    )
    _refs, skip = prepare_merge(DuplicateColourClient(), [style])
    # White (duplicate name) and Black/ Red (punctuation variant) both exist;
    # Navy is genuinely new.
    assert skip == {"SANMAR-W", "SANMAR-BR"}

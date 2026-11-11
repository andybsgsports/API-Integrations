"""Merge-aware preparation for the matrix-item import.

When SanMar styles already exist as NetSuite matrix parents, two things make an
import clean:

1. **Reference parents by internal id** — purely-numeric style names (``2000``)
   can't be resolved by name in ``Subitem Of``; internal ids resolve uniformly.
2. **Skip child combos that already exist** — re-importing a color/size that the
   parent already has triggers "already exists" / "missing price(s)" errors. We
   only want to add genuinely new children.

``prepare_merge`` reads the live catalog once and returns the parent references
plus the set of external ids to skip.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..models import StyleRecord
from ..transform.sizes import normalize_size
from .matrix_options import COLOR_LIST, SIZE_LIST, normalize_option_name
from .parents import existing_child_combos, resolve_parent_ids
from .repository import child_external_id


def _name_to_ids(client, list_type: str, names: Iterable[str]) -> dict[str, set[str]]:
    """Map each wanted option name to EVERY list id that carries it.

    The colour/size lists hold duplicate names -- a retired (inactive) value
    beside its active replacement, e.g. "White" ids 6 (inactive) and 1384 --
    and punctuation variants of one value ("Black/ Red" vs "Black/Red"). An
    existing child may reference ANY of those ids. The old one-id-per-name
    lookup kept whichever duplicate SuiteQL returned last, so when it picked
    the retired id the child that plainly existed was reported as new, sent
    for creation every night, and rejected by NetSuite as a duplicate name
    (PC54-White and ~330 others, nightly, through 2026-11-11). Returning the
    full id set -- matched on the same normalised name the option resolver
    uses -- makes the "already exists" check independent of which duplicate
    an item happens to point at.
    """
    wanted = {normalize_option_name(n) for n in names if n}
    wanted.discard("")
    if not wanted:
        return {}
    rows = client.suiteql(f"SELECT id, name FROM {list_type}")
    out: dict[str, set[str]] = {}
    for r in rows:
        key = normalize_option_name(str(r.get("name") or ""))
        if key in wanted:
            out.setdefault(key, set()).add(str(r["id"]))
    return out


def prepare_merge(client, styles: list[StyleRecord]) -> tuple[dict[str, str], set[str]]:
    """Return ``(parent_refs, skip_external_ids)`` for a merge-aware export.

    ``parent_refs`` maps each style to its parent's internal id (for ``Subitem
    Of``). ``skip_external_ids`` are child external ids whose (color, size) combo
    already exists under the parent and should be left out of the import.
    """
    parent_refs = resolve_parent_ids(client, styles)
    color_ids = _name_to_ids(
        client, COLOR_LIST, (sku.color_name for s in styles for sku in s.skus)
    )
    size_ids = _name_to_ids(
        client, SIZE_LIST, (normalize_size(sku.size) for s in styles for sku in s.skus)
    )

    skip: set[str] = set()
    combos_by_parent: dict[str, set[tuple[str, str]]] = {}
    for style in styles:
        parent_id = parent_refs.get(style.style)
        if parent_id is None:
            continue  # net-new parent: nothing exists to skip
        if parent_id not in combos_by_parent:
            combos_by_parent[parent_id] = existing_child_combos(client, parent_id)
        existing = combos_by_parent[parent_id]
        for sku in style.skus:
            cids = color_ids.get(normalize_option_name(sku.color_name), ())
            sids = size_ids.get(normalize_option_name(normalize_size(sku.size)), ())
            if any((c, z) in existing for c in cids for z in sids):
                skip.add(child_external_id(sku.unique_key))
    return parent_refs, skip

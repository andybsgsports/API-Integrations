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
from .matrix_options import COLOR_LIST, SIZE_LIST
from .parents import existing_child_combos, resolve_parent_ids
from .repository import _sql_escape, child_external_id


def _name_to_id(client, list_type: str, names: Iterable[str]) -> dict[str, str]:
    wanted = sorted({n for n in names if n})
    if not wanted:
        return {}
    inlist = ",".join(f"'{_sql_escape(n)}'" for n in wanted)
    rows = client.suiteql(f"SELECT id, name FROM {list_type} WHERE name IN ({inlist})")
    return {str(r["name"]).casefold(): str(r["id"]) for r in rows}


def prepare_merge(client, styles: list[StyleRecord]) -> tuple[dict[str, str], set[str]]:
    """Return ``(parent_refs, skip_external_ids)`` for a merge-aware export.

    ``parent_refs`` maps each style to its parent's internal id (for ``Subitem
    Of``). ``skip_external_ids`` are child external ids whose (color, size) combo
    already exists under the parent and should be left out of the import.
    """
    parent_refs = resolve_parent_ids(client, styles)
    color_ids = _name_to_id(client, COLOR_LIST, (sku.color_name for s in styles for sku in s.skus))
    size_ids = _name_to_id(
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
            color_id = color_ids.get(sku.color_name.casefold())
            size_id = size_ids.get(normalize_size(sku.size).casefold())
            if color_id and size_id and (color_id, size_id) in existing:
                skip.add(child_external_id(sku.unique_key))
    return parent_refs, skip

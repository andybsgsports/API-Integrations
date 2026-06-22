"""Resolve NetSuite matrix-parent references for SanMar styles.

A purely-numeric style name (e.g. ``2000``) collides with internal ids when used
as a CSV ``Subitem Of`` reference: NetSuite reads the numeric value as an
internal id and links to the wrong record. For those styles we look up the
parent matrix item's internal id and reference *that* instead — a numeric
reference then resolves to the intended parent. Alphanumeric styles (``K420``)
are matched by name and need no resolution.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..models import StyleRecord
from .repository import _sql_escape


def numeric_styles(styles: Iterable[StyleRecord]) -> list[str]:
    """Distinct style names that are purely numeric (need internal-id refs)."""
    return sorted({s.style for s in styles if s.style.strip().isdigit()})


def resolve_numeric_parent_ids(client, styles: Iterable[StyleRecord]) -> dict[str, str]:
    """Map each numeric style to its parent matrix item's internal id.

    Styles whose parent does not exist yet are simply absent from the result
    (their children can't be nested until the parent is created anyway).
    """
    wanted = numeric_styles(styles)
    if not wanted:
        return {}
    inlist = ",".join(f"'{_sql_escape(s)}'" for s in wanted)
    rows = client.suiteql(f"SELECT id, itemid FROM item WHERE itemid IN ({inlist})")
    return {str(r["itemid"]): str(r["id"]) for r in rows if str(r["itemid"]) in set(wanted)}

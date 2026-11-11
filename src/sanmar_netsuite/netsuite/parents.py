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
    return _resolve_parent_ids(client, numeric_styles(styles))


def resolve_parent_ids(client, styles: Iterable[StyleRecord]) -> dict[str, str]:
    """Map every style to its existing parent matrix item's internal id.

    Used for merge-aware exports: referencing all parents by internal id (and
    mapping ``Subitem Of`` by internal id at import) sidesteps the numeric-style
    collision uniformly.
    """
    return _resolve_parent_ids(client, sorted({s.style for s in styles}))


def _resolve_parent_ids(client, names: list[str]) -> dict[str, str]:
    """Map each style name to its parent's internal id, ignoring letter case.

    Item names are not case-sensitive to the people who typed them: the
    catalogue holds parents entered years ago as ``St650`` / ``St5100`` beside
    the feed's ``ST650``. An exact-case lookup missed those, the create leg
    made a second, EMPTY ``ST650`` parent (2026-09-25), and from then on every
    night looked under the empty one, found no children, and re-sent all 190
    -- which the RESTlet quietly updated by external id and the log counted as
    "created" (81 such empty parents in the sandbox, ~3,600 phantom creations
    a night through 2026-11-11).

    So: match on ``UPPER(itemid)``, and when more than one item carries the
    name, take the one that actually has children. Ties fall to the exact-case
    spelling, then the lowest (oldest) id.
    """
    if not names:
        return {}
    inlist = ",".join(f"'{_sql_escape(s.upper())}'" for s in names)
    rows = client.suiteql(
        f"SELECT id, itemid FROM item WHERE UPPER(itemid) IN ({inlist})"
    )
    by_upper: dict[str, list[tuple[str, str]]] = {}
    for r in rows:
        itemid = str(r.get("itemid") or "")
        by_upper.setdefault(itemid.upper(), []).append((str(r["id"]), itemid))

    out: dict[str, str] = {}
    ambiguous: dict[str, list[tuple[str, str]]] = {}
    for name in names:
        hits = by_upper.get(name.upper(), [])
        if len(hits) == 1:
            out[name] = hits[0][0]
        elif len(hits) > 1:
            ambiguous[name] = hits
    if ambiguous:
        ids = sorted({pid for hits in ambiguous.values() for pid, _ in hits}, key=int)
        counts: dict[str, int] = {}
        for i in range(0, len(ids), 500):
            chunk = ",".join(ids[i:i + 500])
            for r in client.suiteql(
                "SELECT parent, COUNT(*) AS n FROM item "
                f"WHERE parent IN ({chunk}) GROUP BY parent"
            ):
                counts[str(r["parent"])] = int(r.get("n") or 0)
        for name, hits in ambiguous.items():
            out[name] = min(hits, key=lambda h: _twin_rank(h, name, counts))[0]
    return out


def _twin_rank(hit: tuple[str, str], name: str, counts: dict[str, int]) -> tuple[int, bool, int]:
    """Sort key among same-named parents: most children, exact spelling, oldest."""
    pid, itemid = hit
    return (-counts.get(pid, 0), itemid != name, int(pid))


def existing_child_combos(client, parent_id: str) -> set[tuple[str, str]]:
    """The set of (color id, size id) combinations already under a parent.

    Reads the matrix option values with a plain SELECT (filtering on the custom
    field is unreliable in SuiteQL, but selecting it is fine).
    """
    rows = client.suiteql(
        "SELECT custitem_bsg_color AS color, custitem_bsg_size AS size "
        f"FROM item WHERE parent = {int(parent_id)}"
    )
    return {
        (str(r["color"]), str(r["size"]))
        for r in rows
        if r.get("color") and r.get("size")
    }

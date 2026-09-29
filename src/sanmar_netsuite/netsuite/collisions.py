"""Read-only pre-flight: find item names NetSuite would reject as duplicates.

NetSuite refuses a new matrix child whose name (child segment) already exists
under the same parent -- even if the existing record is inactive, and even if it
carries a different external id, so the externalId-based "already present"
check in ``prepare_merge`` cannot see it. On 2026-09-29 the create leg posted
~4000 children and 324 came back "Uniqueness error", which failed the whole
night. This module asks NetSuite up front (SuiteQL only -- it never writes) so
those children are skipped and reported instead of posted and failed.

Matrix children store parent-prefixed itemids ('TSK11 : TSK11-Cardinal-Large')
while the uniqueness check compares the child segment, so we match the child
name exactly OR as the segment after ' : ' (case-insensitive).
"""

from __future__ import annotations

from typing import Any

from .repository import _sql_escape

CHUNK = 40


def _child_segment(itemid: str) -> str:
    return itemid.rsplit(" : ", 1)[-1].strip().upper()


def find_name_collisions(client: Any, names: list[str]) -> dict[str, dict]:
    """Return {NAME_UPPER: {"id", "itemid", "inactive"}} for names that exist.

    Read-only. ``names`` are the child names we are about to post.
    """
    wanted = {n.strip().upper(): n for n in names if n}
    found: dict[str, dict] = {}
    ordered = sorted(wanted)
    for i in range(0, len(ordered), CHUNK):
        chunk = ordered[i:i + CHUNK]
        conds = " OR ".join(
            f"UPPER(itemid) LIKE '%{_sql_escape(n)}'" for n in chunk
        )
        rows = client.suiteql(f"SELECT id, itemid, isinactive FROM item WHERE {conds}")
        for r in rows:
            seg = _child_segment(str(r.get("itemid") or ""))
            if seg in wanted and seg not in found:
                found[seg] = {
                    "id": str(r.get("id")),
                    "itemid": str(r.get("itemid")),
                    "inactive": str(r.get("isinactive")).upper() in ("T", "TRUE"),
                }
    return found


def split_payloads(payloads: list[dict], collisions: dict[str, dict]):
    """Split payloads into (to_post, skipped) by item-name collision."""
    to_post: list[dict] = []
    skipped: list[tuple[dict, dict]] = []
    for pl in payloads:
        hit = collisions.get(str(pl.get("itemId") or "").strip().upper())
        if hit:
            skipped.append((pl, hit))
        else:
            to_post.append(pl)
    return to_post, skipped

"""Feed-presence heartbeat shared by every supplier writer.

Each nightly writer stamps ``custitem_feed_source`` / ``custitem_feed_last_seen``
on the items it matches in its feed; ``scripts/item_lifecycle.py`` later
inactivates items whose stamp goes stale past the grace period (discontinued),
and reactivates items that reappear.

To keep the diff-aware writers from PATCHing every matched item every night
just to bump a date, the stamp only refreshes once it is ``STALE_AFTER_DAYS``
old -- comfortably inside the lifecycle grace period, at roughly a third of
the write volume.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

SOURCE_FIELD = "custitem_feed_source"
LAST_SEEN_FIELD = "custitem_feed_last_seen"
FIELDS = [SOURCE_FIELD, LAST_SEEN_FIELD]

#: Refresh the last-seen stamp once it's this old (days).
STALE_AFTER_DAYS = 3
#: Inactivate an item once its stamp is this old (days) -- item_lifecycle.py.
GRACE_DAYS = 7

# SuiteQL returns dates in the account's display format; this sandbox uses
# US M/D/YYYY. ISO is what we PATCH, so accept it back too.
_DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y")


def parse_ns_date(raw: Any) -> date | None:
    s = str(raw or "").strip()
    if not s:
        return None
    s = s.split("T")[0].split(" ")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def stamp(want: dict[str, Any], row: dict[str, Any], source: str) -> None:
    """Add heartbeat fields to ``want`` when they need (re)writing.

    ``row`` is the item's current-values row from the writer's bulk SuiteQL
    read (must include the two heartbeat columns, lowercase keys).
    """
    if str(row.get(SOURCE_FIELD.lower()) or "").strip() != source:
        want[SOURCE_FIELD] = source
    seen = parse_ns_date(row.get(LAST_SEEN_FIELD.lower()))
    today = date.today()
    if seen is None or (today - seen) >= timedelta(days=STALE_AFTER_DAYS):
        want[LAST_SEEN_FIELD] = today.isoformat()

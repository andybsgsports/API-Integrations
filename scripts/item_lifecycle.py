"""Auto-inactivate discontinued items / reactivate returning ones (CI).

Every feed writer stamps ``custitem_feed_source`` + ``custitem_feed_last_seen``
on the items it matches (see ``sanmar_netsuite.netsuite.feed_seen``). This job
runs nightly after all writers:

* stamp stale for ``GRACE_DAYS`` -> the supplier dropped the item; mark it
  inactive (discontinued).
* stamp fresh but the item is inactive -> it returned to the feed; reactivate.
* matrix parents (never stamped themselves): inactive iff all their children
  are inactive; reactivated when any child is active again.

Safety rails:
* Items with no ``custitem_feed_source`` are NEVER touched -- only items a
  feed writer has explicitly claimed participate.
* Circuit breaker: if more than ``LIFECYCLE_MAX_PCT`` percent of a source's
  stamped items would inactivate in one run (default 30), that source is
  skipped and flagged -- a broken feed or a writer outage looks exactly like
  a mass discontinuation, and the nightly failure alert is the right channel
  for that, not a catalog wipe.

Honors ``SYNC_DRY_RUN``; ``UPDATE_MAX_ITEMS`` caps writes.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, timedelta

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.feed_seen import (
    GRACE_DAYS,
    LAST_SEEN_FIELD,
    SOURCE_FIELD,
    parse_ns_date,
)


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    max_pct = float(os.environ.get("LIFECYCLE_MAX_PCT", "30") or "30")
    client = NetSuiteClient(cfg.netsuite)
    today = date.today()
    cutoff = today - timedelta(days=GRACE_DAYS)

    try:
        rows = client.suiteql(
            f"SELECT id, itemid, isinactive, parent, {SOURCE_FIELD} AS src, "
            f"{LAST_SEEN_FIELD} AS seen FROM item WHERE {SOURCE_FIELD} IS NOT NULL"
        )
    except Exception as exc:  # noqa: BLE001
        # Heartbeat fields not created yet (ns_field_setup.py) -- nothing can
        # be stamped, so there is legitimately nothing to do.
        print(f"heartbeat columns unavailable ({str(exc)[:80]}); "
              "run ns-field-setup first. Nothing to do.")
        return 0
    print(f"items with a feed stamp: {len(rows):,} (grace: {GRACE_DAYS} days, "
          f"cutoff {cutoff.isoformat()})")

    by_source: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_source[str(r.get("src") or "?")].append(r)

    to_inactivate: list[tuple[str, str]] = []  # (id, itemid)
    to_reactivate: list[tuple[str, str]] = []
    for src, items in sorted(by_source.items()):
        stale = fresh_inactive = unparsed = 0
        src_inactivate: list[tuple[str, str]] = []
        for r in items:
            seen = parse_ns_date(r.get("seen"))
            inactive = str(r.get("isinactive") or "F").upper() == "T"
            if seen is None:
                unparsed += 1
                continue
            if seen < cutoff and not inactive:
                stale += 1
                src_inactivate.append((str(r["id"]), str(r.get("itemid") or "")))
            elif seen >= cutoff and inactive:
                fresh_inactive += 1
                to_reactivate.append((str(r["id"]), str(r.get("itemid") or "")))
        pct = (100.0 * stale / len(items)) if items else 0.0
        note = ""
        if stale and pct > max_pct:
            note = (f"  << CIRCUIT BREAKER: {pct:.0f}% > {max_pct:.0f}% -- "
                    f"skipping this source's inactivations (feed outage?)")
            src_inactivate = []
        print(f"  {src:<10} stamped: {len(items):>6,}  stale: {stale:>5,} "
              f"({pct:.1f}%)  returning: {fresh_inactive:>4,}  "
              f"unparsed-date: {unparsed}{note}")
        to_inactivate.extend(src_inactivate)

    print(f"\nto inactivate: {len(to_inactivate):,}; to reactivate: {len(to_reactivate):,}")

    considered = written = failures = 0

    def apply(batch: list[tuple[str, str]], inactive: bool, verb: str) -> None:
        nonlocal considered, written, failures
        shown = 0
        for rid, itemid in batch:
            if max_items and considered >= max_items:
                return
            considered += 1
            if shown < 10:
                shown += 1
                print(f"  {verb}: {itemid!r} (id {rid})")
            if not allow_write:
                written += 1
                continue
            try:
                client.update_record("inventoryItem", rid, {"isInactive": inactive})
                written += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if failures <= 10:
                    detail = getattr(exc, "payload", "")
                    print(f"  FAILED {itemid!r}: {str(exc)[:100]} :: {str(detail)[:200]}")

    # reactivate first: a returning item must be active before tonight's
    # writers can PATCH its fields again.
    apply(to_reactivate, False, "reactivate")
    apply(to_inactivate, True, "inactivate")

    # -- matrix parents: follow their children ------------------------------
    parents = sorted({str(r["parent"]) for r in rows if r.get("parent")}, key=int)
    parent_flips: list[tuple[str, str, bool]] = []
    if parents:
        kids: dict[str, list[bool]] = defaultdict(list)
        for i in range(0, len(parents), 250):
            in_list = ", ".join(parents[i : i + 250])
            for r in client.suiteql(
                f"SELECT id, parent, isinactive FROM item WHERE parent IN ({in_list})"
            ):
                kids[str(r["parent"])].append(
                    str(r.get("isinactive") or "F").upper() == "T"
                )
        for i in range(0, len(parents), 250):
            in_list = ", ".join(parents[i : i + 250])
            for r in client.suiteql(
                f"SELECT id, itemid, isinactive FROM item WHERE id IN ({in_list})"
            ):
                pid = str(r["id"])
                p_inactive = str(r.get("isinactive") or "F").upper() == "T"
                child_states = kids.get(pid, [])
                if not child_states:
                    continue
                all_inactive = all(child_states)
                if all_inactive and not p_inactive:
                    parent_flips.append((pid, str(r.get("itemid") or ""), True))
                elif not all_inactive and p_inactive:
                    parent_flips.append((pid, str(r.get("itemid") or ""), False))
    print(f"matrix parents needing a flip: {len(parent_flips):,} "
          f"(of {len(parents):,} parents with stamped children)")
    apply([(p, n) for p, n, inact in parent_flips if not inact], False, "reactivate parent")
    apply([(p, n) for p, n, inact in parent_flips if inact], True, "inactivate parent")

    verb = "updated" if allow_write else "WOULD update (dry run)"
    print(f"\nitem lifecycle: {verb} {written} item(s); failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

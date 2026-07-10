"""One-time back-fill: stamp SanMar keys onto the existing NetSuite catalog.

Consumes a :class:`~sanmar_netsuite.netsuite.adopt.ReconcileReport` and writes,
for every matched item:

* **UPC** (``upcCode``) — the feed GTIN, Andy's primary ask: once present, feed
  rows and NetSuite items line up by barcode forever.
* **External id** (``SANMAR-<unique_key>``) — optional (``set_external_ids``):
  re-keys the item to this integration's convention so the nightly sync can
  match it. Off by default because it *overwrites* the migrated numeric
  external ids, which other tooling may still reference.

And applies the **color-rename plan**: list values matched via abbreviation get
their Name set to SanMar's full color (renaming a list value cascades to every
item that uses it). Renames that differ only in whitespace are skipped so we
don't churn names over SanMar's stray spaces (``Steel/Black`` vs ``Steel/ Black``).

All writes honor ``allow_write`` (wired to ``SYNC_DRY_RUN``) and ``max_items``
so the first live run can be a small smoke test.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .adopt import COLOR_LIST, MatchRow, ReconcileReport

log = logging.getLogger(__name__)

ITEM_RECORD_TYPE = "inventoryItem"


def names_equivalent(a: str, b: str) -> bool:
    """True when two color names differ only by case/whitespace."""
    return a.replace(" ", "").casefold() == b.replace(" ", "").casefold()


@dataclass
class BackfillResult:
    allow_write: bool
    items_considered: int = 0  # distinct matched items with something to write
    items_written: int = 0  # written (or would-write in dry run)
    items_skipped_no_gtin: int = 0  # matched but feed carries no GTIN
    items_conflict: int = 0  # extra feed rows pointing at an already-claimed item
    renames_written: int = 0  # written (or would-write in dry run)
    renames_skipped_ws: int = 0  # whitespace-only differences
    failures: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        verb = "wrote" if self.allow_write else "WOULD write (dry run)"
        lines = [
            f"backfill: {verb} {self.items_written}/{self.items_considered} item(s)",
            f"  matched items without a feed GTIN: {self.items_skipped_no_gtin}",
            f"  duplicate feed rows for an already-claimed item: {self.items_conflict}",
            f"  color renames {verb}: {self.renames_written} "
            f"(whitespace-only skipped: {self.renames_skipped_ws})",
            f"  failures: {len(self.failures)}",
        ]
        for target, err in self.failures[:10]:
            lines.append(f"    {target}: {err}")
        return "\n".join(lines)


def backfill_keys(
    client,
    report: ReconcileReport,
    *,
    allow_write: bool,
    max_items: int = 0,
    set_external_ids: bool = False,
    rename_colors: bool = True,
) -> BackfillResult:
    """Write UPCs (+ optionally external ids) onto matched items, rename colors."""
    result = BackfillResult(allow_write=allow_write)

    # One write per NetSuite item: keep the first feed row that claimed it.
    by_item: dict[str | None, MatchRow] = {}
    for row in report.matched:
        if row.ns_id in by_item:
            result.items_conflict += 1
            continue
        by_item[row.ns_id] = row

    for ns_id, row in by_item.items():
        body: dict[str, str] = {}
        if row.gtin:
            body["upcCode"] = row.gtin
        else:
            result.items_skipped_no_gtin += 1
        if set_external_ids:
            body["externalId"] = f"SANMAR-{row.unique_key}"
        if not body:
            continue
        if max_items and result.items_considered >= max_items:
            break
        result.items_considered += 1
        if not allow_write:
            result.items_written += 1
            log.debug("[dry-run] item %s <- %s", ns_id, body)
            continue
        try:
            client.update_record(ITEM_RECORD_TYPE, ns_id, body)
            result.items_written += 1
        except Exception as exc:  # noqa: BLE001 - tally and continue
            result.failures.append((f"item {ns_id}", str(exc)[:200]))

    if rename_colors:
        for vid, (cur, new) in sorted(report.color_renames.items()):
            if names_equivalent(cur, new):
                result.renames_skipped_ws += 1
                continue
            if not allow_write:
                result.renames_written += 1
                log.debug("[dry-run] color %s: %r -> %r", vid, cur, new)
                continue
            try:
                client.update_record(COLOR_LIST, vid, {"name": new})
                result.renames_written += 1
            except Exception as exc:  # noqa: BLE001
                result.failures.append((f"color {vid}", str(exc)[:200]))

    return result

"""Shared exit-code policy for the nightly writers.

Every writer used to return ``1 if failures else 0``, so a couple of throttled
records out of tens of thousands filed a "pipeline failed" issue. Seven such
issues in four days (#93-#98) is how the failures that matter get ignored.

The rule, applied identically everywhere so one vendor's runs aren't noisier
than another's:

* a **small, entirely-429** failure set is transient -- the client already
  retried with backoff, every writer is diff-aware, and the next run picks up
  those exact records. Report success, with a NOTE naming the counts.
* **any non-429 failure is fatal**, no matter how small. A single bad field
  value silently rejected ~45k records twice (an over-length stockdescription
  on 2026-07-22, a relative image URL on 2026-08-05); those must stay loud.
"""

from __future__ import annotations

import os
import time

#: Absolute floor for "small" -- below this, a 429 set is noise at any volume.
MIN_TOLERATED = 50
#: ...and above the floor, it must also be under this share of the attempts.
MAX_TOLERATED_SHARE = 0.01
#: Skipping more chunks than this means a whole slice went unconsidered.
MAX_TOLERATED_CHUNKS = 2


def is_transient_throttling(
    failures: int, non_429_failures: int, attempted: int, chunks_skipped: int = 0
) -> bool:
    """Is this failure set a small, self-healing throttling residue?"""
    if failures <= 0:
        return chunks_skipped == 0
    if non_429_failures > 0 or chunks_skipped > MAX_TOLERATED_CHUNKS:
        return False
    return failures <= max(MIN_TOLERATED, int(attempted * MAX_TOLERATED_SHARE))


def exit_code(
    label: str,
    failures: int,
    non_429_failures: int,
    attempted: int,
    chunks_skipped: int = 0,
) -> int:
    """0 / 1 for a writer's ``main()``, printing a NOTE when it forgives 429s."""
    if not failures and not chunks_skipped:
        return 0
    if is_transient_throttling(failures, non_429_failures, attempted, chunks_skipped):
        print(
            f"NOTE: {label}: all {failures} failure(s) were 429 throttling "
            f"({chunks_skipped} chunk(s) skipped) -- transient, the next "
            f"diff-aware run heals them; treating the run as a success."
        )
        return 0
    return 1


def print_field_counts(label: str, counts: dict[str, int], top: int = 12) -> None:
    """One line naming which fields a writer changed, most-changed first.

    A writer that reports "wrote 583 item(s)" every night says nothing about
    WHAT it keeps writing; this line does (inventory quantities are expected
    nightly, a price or a name rewritten every night is a bug).
    """
    if not counts:
        return
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])
    shown = ", ".join(f"{k} {n:,}" for k, n in ordered[:top])
    more = f", and {len(ordered) - top} more field(s)" if len(ordered) > top else ""
    print(f"NOTE: {label} fields changed: {shown}{more}")


def rotated(n: int, day_ordinal: int, step: int = 7) -> list[int]:
    """Indexes 0..n-1 starting from a point that moves every day.

    A pass that is cut off by a time limit always loses its tail. Starting
    from a different place each day means the part that is lost changes, so
    every item is reached within a few days instead of the same items never
    being reached at all.
    """
    if n <= 0:
        return []
    start = (day_ordinal * max(1, n // step)) % n
    return list(range(start, n)) + list(range(0, start))


def leg_deadline() -> float | None:
    """Wall-clock time (epoch seconds) this workflow leg must stop writing by.

    ``vendor_pipeline.py`` sets ``LEG_DEADLINE_EPOCH`` once, from
    ``LEG_TIME_BUDGET_MIN``, when the leg starts, so every script in the leg
    measures against the same clock. A step that is still writing when the
    job limit arrives is cancelled with nothing reported; one that stops at
    the deadline says what it did not reach and the next run carries on.
    """
    raw = os.environ.get("LEG_DEADLINE_EPOCH", "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def past_deadline(deadline: float | None) -> bool:
    return deadline is not None and time.time() >= deadline

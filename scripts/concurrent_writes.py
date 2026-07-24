"""Bounded-concurrency record writes against the NetSuite REST client.

Writing tens of thousands of records one-at-a-time is throttle-bound and can run
for hours. NetSuite governs *concurrent* requests (typically 5-15 for REST), so
issuing a small number of writes in parallel -- while the client's own 429/5xx
backoff still smooths over the limit -- finishes far faster without exceeding
the account's budget. The client is safe to call from multiple threads: a
``requests.Session`` plus per-request OAuth1 signing and per-call retry, with no
shared mutable state.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed

# Default kept modest so a single job stays inside a standard account's
# concurrency limit even if a nightly overlaps; override per run if needed.
DEFAULT_WORKERS = int(os.environ.get("NETSUITE_WRITE_CONCURRENCY", "5") or "5")

# NetSuite validates a PATCH as a unit: one unacceptable field rejects the whole
# record, taking every other field on it down too. That turned a single bad
# value into "0 of 45,531 items written" twice in three days -- once on an
# over-length stockdescription, then again on a weightunit enum. Rather than
# lose an entire catalogue refresh to one field, we identify the field NetSuite
# named, drop it, and retry the rest of the record once.
#
# The two shapes NetSuite uses to name the offender:
#   "You have entered an Invalid Field Value lb for the following field: weightunit."
#   "The field stockdescription contained more than the maximum number ( 21 ) ..."
_FIELD_PATTERNS = (
    re.compile(r"for the following field:\s*([A-Za-z0-9_]+)"),
    re.compile(r"\bThe field\s+([A-Za-z0-9_]+)\s"),
)


def _offending_fields(exc: Exception) -> set[str]:
    """Lowercased field names NetSuite blamed, from its error payload."""
    payload = getattr(exc, "payload", None)
    text = str(payload) if payload else str(exc)
    found: set[str] = set()
    for pattern in _FIELD_PATTERNS:
        found.update(m.lower() for m in pattern.findall(text))
    return found


def write_records(
    client,
    record_type: str,
    jobs: Iterable[tuple[str, dict]],
    *,
    workers: int | None = None,
    on_error: Callable[[str, Exception], None] | None = None,
    dropped: dict[str, int] | None = None,
) -> tuple[int, int]:
    """PATCH each ``(internal_id, body)`` job concurrently.

    Returns ``(written, failures)``. ``on_error(internal_id, exc)`` is called for
    each failure (after the client's own retries are exhausted) so callers can
    log a sample. Order is not preserved; each write is independent.

    When NetSuite rejects a record and names the offending field, that field is
    removed and the record retried once, so a single bad value costs that one
    field instead of the entire record. Pass ``dropped`` to receive
    ``{field: count}`` for everything skipped this way -- callers should report
    it, since a silently dropped field is exactly the kind of gap that hides
    for days.
    """
    jobs = list(jobs)
    if not jobs:
        return 0, 0
    n = max(1, workers or DEFAULT_WORKERS)
    written = failures = 0
    # Worker threads share the counter, and "read, add one, store" is not
    # atomic -- without this, concurrent drops of the same field lose counts.
    drop_lock = threading.Lock()

    def _do(job: tuple[str, dict]) -> str:
        rid, body = job
        try:
            client.update_record(record_type, rid, body)
            return rid
        except Exception as exc:  # noqa: BLE001 - salvage the rest of the record
            blamed = _offending_fields(exc)
            if not blamed:
                raise
            # Body keys are camelCase; NetSuite names them lowercased.
            strip = {k for k in body if k.lower() in blamed}
            trimmed = {k: v for k, v in body.items() if k not in strip}
            if not strip or not trimmed:
                raise
            client.update_record(record_type, rid, trimmed)
            if dropped is not None:
                with drop_lock:
                    for k in strip:
                        dropped[k] = dropped.get(k, 0) + 1
            return rid

    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = {ex.submit(_do, j): j for j in jobs}
        for fut in as_completed(futs):
            try:
                fut.result()
                written += 1
            except Exception as exc:  # noqa: BLE001 - report, keep going
                failures += 1
                if on_error is not None:
                    on_error(futs[fut][0], exc)
    return written, failures

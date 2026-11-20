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

import atexit
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed

# Since the netsuite-writes concurrency group serialises the writers (only one
# workflow ever writes at a time), a single job can use most of the account's
# concurrent-request budget itself. Raised 5 -> 8 on 2026-07-30 to speed the
# multi-hour full passes; the client's 429/5xx backoff absorbs overshoot. NOT
# yet validated under load -- if the next runs' logs show sustained 429s,
# dial back via NETSUITE_WRITE_CONCURRENCY.
DEFAULT_WORKERS = int(os.environ.get("NETSUITE_WRITE_CONCURRENCY", "8") or "8")

# The account's real concurrent-request limit is not known in advance and is
# shared with staff and other integrations. On 2026-11-19 the SanMar update ran
# 8 writers and took 24,044 throttle (429) responses for about 57,500 writes;
# each one parks a writer for 2 to 16 seconds. The gate below starts at the
# configured number of writers, steps down when writes are throttled, and tries
# one more writer again after a run of clean writes, so a run settles at what
# the account will actually take. NETSUITE_WRITE_ADAPTIVE=off restores the
# fixed number.
ADAPTIVE = os.environ.get("NETSUITE_WRITE_ADAPTIVE", "on").strip().lower() not in (
    "off", "0", "false", "no")


class AdaptiveGate:
    """Limits how many writes are in flight, moving with the throttling seen."""

    #: After a step down, ignore further throttles for this long: the writes
    #: already in flight were sent under the old limit.
    COOLDOWN_S = 5.0
    #: Clean writes in a row before trying one more writer.
    PROBE_AFTER = 200
    #: A failed probe doubles the wait before the next one, up to this.
    PROBE_MAX = 6400

    def __init__(self, max_workers: int, *, clock: Callable[[], float] = time.monotonic):
        self.max = max(1, max_workers)
        self.limit = self.max
        self._active = 0
        self._clean = 0
        self._probe_after = self.PROBE_AFTER
        self._probing = False
        self._last_cut = float("-inf")
        self._clock = clock
        self._cond = threading.Condition()
        self.throttled = 0
        self.writes = 0
        self.lowest = self.max

    def __enter__(self) -> AdaptiveGate:
        with self._cond:
            while self._active >= self.limit:
                self._cond.wait()
            self._active += 1
        return self

    def __exit__(self, *exc: object) -> None:
        with self._cond:
            self._active -= 1
            self._cond.notify_all()

    def record(self, throttled: bool) -> None:
        """Report one finished write: was it throttled along the way?"""
        with self._cond:
            self.writes += 1
            if throttled:
                self.throttled += 1
                self._clean = 0
                now = self._clock()
                if now - self._last_cut >= self.COOLDOWN_S and self.limit > 1:
                    if self._probing:
                        self._probe_after = min(self.PROBE_MAX, self._probe_after * 2)
                    self.limit -= 1
                    self.lowest = min(self.lowest, self.limit)
                    self._last_cut = now
                self._probing = False
                return
            self._clean += 1
            if self._clean >= self._probe_after and self.limit < self.max:
                self.limit += 1
                self._clean = 0
                self._probing = True
                self._cond.notify_all()
            elif self._probing and self._clean >= self.limit * 4:
                self._probing = False  # the extra writer held


# One gate per client for the life of the process: the writers call
# write_records once per chunk, and what was learned about the account's limit
# in one chunk must carry into the next.
_GATES: dict[int, AdaptiveGate] = {}
_GATES_LOCK = threading.Lock()


def _gate_for(client, workers: int) -> AdaptiveGate:
    with _GATES_LOCK:
        gate = _GATES.get(id(client))
        if gate is None or gate.max != workers:
            gate = _GATES[id(client)] = AdaptiveGate(workers)
        return gate


def throttle_report() -> str | None:
    """One line on how the write concurrency moved, or None if never throttled."""
    gates = [g for g in _GATES.values() if g.throttled]
    if not gates:
        return None
    return "; ".join(
        f"started at {g.max}, lowest {g.lowest}, ended at {g.limit}, "
        f"{g.throttled:,} of {g.writes:,} write(s) throttled at least once"
        for g in gates)


@atexit.register
def _print_throttle_report() -> None:
    line = throttle_report()
    if line:
        print(f"NOTE: write concurrency: {line}", flush=True)


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
    count_429 = getattr(client, "throttle_count", None)
    gate = _gate_for(client, n) if ADAPTIVE and callable(count_429) else None

    def _do(job: tuple[str, dict]) -> str:
        if gate is None:
            return _write(job)
        before = count_429()
        try:
            with gate:
                return _write(job)
        finally:
            gate.record(count_429() > before)

    def _write(job: tuple[str, dict]) -> str:
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

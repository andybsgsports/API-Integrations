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
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed

# Default kept modest so a single job stays inside a standard account's
# concurrency limit even if a nightly overlaps; override per run if needed.
DEFAULT_WORKERS = int(os.environ.get("NETSUITE_WRITE_CONCURRENCY", "5") or "5")


def write_records(
    client,
    record_type: str,
    jobs: Iterable[tuple[str, dict]],
    *,
    workers: int | None = None,
    on_error: Callable[[str, Exception], None] | None = None,
) -> tuple[int, int]:
    """PATCH each ``(internal_id, body)`` job concurrently.

    Returns ``(written, failures)``. ``on_error(internal_id, exc)`` is called for
    each failure (after the client's own retries are exhausted) so callers can
    log a sample. Order is not preserved; each write is independent.
    """
    jobs = list(jobs)
    if not jobs:
        return 0, 0
    n = max(1, workers or DEFAULT_WORKERS)
    written = failures = 0

    def _do(job: tuple[str, dict]) -> str:
        rid, body = job
        client.update_record(record_type, rid, body)
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

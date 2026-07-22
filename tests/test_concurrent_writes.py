"""Tests for scripts/concurrent_writes.write_records (bounded-concurrency writes)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from concurrent_writes import write_records  # noqa: E402


class _FakeClient:
    """Records update_record calls; raises for ids in ``fail``."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.seen: list[tuple[str, str, dict]] = []
        self._lock = threading.Lock()

    def update_record(self, record_type: str, rid: str, body: dict) -> None:
        with self._lock:
            self.seen.append((record_type, rid, body))
        if rid in self.fail:
            raise RuntimeError(f"429 for {rid}")


def test_all_succeed():
    client = _FakeClient()
    jobs = [(str(i), {"cost": i}) for i in range(50)]
    written, failures = write_records(client, "inventoryItem", jobs, workers=8)
    assert (written, failures) == (50, 0)
    assert len(client.seen) == 50
    assert {r[1] for r in client.seen} == {str(i) for i in range(50)}


def test_failures_counted_and_reported():
    client = _FakeClient(fail={"3", "7"})
    errors: list[str] = []
    jobs = [(str(i), {"x": i}) for i in range(10)]
    written, failures = write_records(
        client, "inventoryItem", jobs, workers=4, on_error=lambda rid, exc: errors.append(rid)
    )
    assert (written, failures) == (8, 2)
    assert sorted(errors) == ["3", "7"]


def test_empty_jobs_no_work():
    client = _FakeClient()
    assert write_records(client, "inventoryItem", []) == (0, 0)
    assert client.seen == []

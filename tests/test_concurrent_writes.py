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


class _FieldRejectingClient:
    """Rejects any body containing ``bad`` the way NetSuite does.

    NetSuite validates a record as a unit, so one unacceptable field rejects
    every other field on it too -- the behaviour that turned a single bad value
    into "0 of 45,531 items written" twice.
    """

    def __init__(self, bad: str, detail: str) -> None:
        self.bad = bad
        self.detail = detail
        self.seen: list[dict] = []
        self._lock = threading.Lock()

    def update_record(self, record_type: str, rid: str, body: dict) -> None:
        with self._lock:
            self.seen.append(dict(body))
        if self.bad in body:
            exc = RuntimeError("NetSuite 400: Bad Request")
            exc.payload = {"o:errorDetails": [{"detail": self.detail}]}  # type: ignore[attr-defined]
            raise exc


def test_invalid_field_value_is_dropped_and_record_retried():
    client = _FieldRejectingClient(
        "weightUnit",
        "You have entered an Invalid Field Value lb for the following "
        "field: weightunit.",
    )
    dropped: dict[str, int] = {}
    jobs = [(str(i), {"cost": 1.0, "weightUnit": "lb"}) for i in range(5)]
    written, failures = write_records(
        client, "inventoryItem", jobs, workers=3, dropped=dropped
    )
    # The record survives; only the offending field is lost.
    assert (written, failures) == (5, 0)
    assert dropped == {"weightUnit": 5}
    retried = [b for b in client.seen if "weightUnit" not in b]
    assert len(retried) == 5
    assert all(b == {"cost": 1.0} for b in retried)


def test_over_length_field_is_dropped_and_record_retried():
    client = _FieldRejectingClient(
        "stockDescription",
        "The field stockdescription contained more than the maximum "
        "number ( 21 ) of characters allowed.",
    )
    dropped: dict[str, int] = {}
    jobs = [("1", {"cost": 2.0, "stockDescription": "x" * 40})]
    written, failures = write_records(client, "inventoryItem", jobs, dropped=dropped)
    assert (written, failures) == (1, 0)
    assert dropped == {"stockDescription": 1}


def test_unattributable_error_still_fails():
    """A failure NetSuite doesn't pin on a field must not be silently swallowed."""
    client = _FakeClient(fail={"1"})
    dropped: dict[str, int] = {}
    written, failures = write_records(
        client, "inventoryItem", [("1", {"cost": 1.0})], dropped=dropped
    )
    assert (written, failures) == (0, 1)
    assert dropped == {}


def test_record_of_only_bad_fields_is_not_retried_empty():
    """Stripping everything would PATCH an empty body -- report it instead."""
    client = _FieldRejectingClient(
        "weightUnit", "Invalid Field Value lb for the following field: weightunit."
    )
    dropped: dict[str, int] = {}
    written, failures = write_records(
        client, "inventoryItem", [("1", {"weightUnit": "lb"})], dropped=dropped
    )
    assert (written, failures) == (0, 1)
    assert dropped == {}

"""Tests for the adaptive write gate and the leg deadline."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import concurrent_writes  # noqa: E402
import run_status  # noqa: E402
from concurrent_writes import AdaptiveGate, write_records  # noqa: E402


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_gate_steps_down_once_per_cooldown():
    clock = _Clock()
    gate = AdaptiveGate(8, clock=clock)
    for _ in range(5):  # a burst from writes already in flight
        gate.record(True)
    assert gate.limit == 7
    clock.t += AdaptiveGate.COOLDOWN_S
    gate.record(True)
    assert gate.limit == 6
    assert gate.lowest == 6 and gate.throttled == 6


def test_gate_never_goes_below_one():
    clock = _Clock()
    gate = AdaptiveGate(2, clock=clock)
    for _ in range(10):
        clock.t += AdaptiveGate.COOLDOWN_S
        gate.record(True)
    assert gate.limit == 1


def test_gate_probes_up_and_backs_off_longer_after_a_failed_probe():
    clock = _Clock()
    gate = AdaptiveGate(8, clock=clock)
    gate.record(True)
    assert gate.limit == 7
    for _ in range(AdaptiveGate.PROBE_AFTER):
        gate.record(False)
    assert gate.limit == 8  # tried one more writer
    clock.t += AdaptiveGate.COOLDOWN_S
    gate.record(True)  # the probe was throttled
    assert gate.limit == 7
    for _ in range(AdaptiveGate.PROBE_AFTER):
        gate.record(False)
    assert gate.limit == 7  # waits twice as long before trying again
    for _ in range(AdaptiveGate.PROBE_AFTER):
        gate.record(False)
    assert gate.limit == 8


def test_gate_never_exceeds_configured_writers():
    gate = AdaptiveGate(4)
    for _ in range(AdaptiveGate.PROBE_AFTER * 3):
        gate.record(False)
    assert gate.limit == 4


class _ThrottlingClient:
    """Throttles a write whenever more than ``cap`` are in flight."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self._lock = threading.Lock()
        self._tl = threading.local()
        self.in_flight = 0
        self.peak_after_settle = 0
        self.calls = 0

    def throttle_count(self) -> int:
        return getattr(self._tl, "n", 0)

    def update_record(self, record_type: str, rid: str, body: dict) -> None:
        with self._lock:
            self.in_flight += 1
            self.calls += 1
            over = self.in_flight > self.cap
            if self.calls > 400:
                self.peak_after_settle = max(self.peak_after_settle, self.in_flight)
        try:
            if over:
                self._tl.n = getattr(self._tl, "n", 0) + 1
            threading.Event().wait(0.001)
        finally:
            with self._lock:
                self.in_flight -= 1


def test_write_records_settles_under_the_account_limit(monkeypatch):
    monkeypatch.setattr(AdaptiveGate, "COOLDOWN_S", 0.0)
    monkeypatch.setattr(concurrent_writes, "_GATES", {})
    client = _ThrottlingClient(cap=3)
    jobs = [(str(i), {"x": i}) for i in range(600)]
    written, failures = write_records(client, "inventoryItem", jobs, workers=8)
    assert (written, failures) == (600, 0)
    gate = concurrent_writes._GATES[id(client)]
    assert gate.lowest <= 3
    assert client.peak_after_settle <= 4  # at most one probing writer over
    assert "throttled at least once" in concurrent_writes.throttle_report()


def test_gate_is_kept_between_calls(monkeypatch):
    monkeypatch.setattr(AdaptiveGate, "COOLDOWN_S", 0.0)
    monkeypatch.setattr(concurrent_writes, "_GATES", {})
    client = _ThrottlingClient(cap=2)
    write_records(client, "inventoryItem", [(str(i), {}) for i in range(100)], workers=8)
    first = concurrent_writes._GATES[id(client)]
    learned = first.limit
    write_records(client, "inventoryItem", [("a", {})], workers=8)
    assert concurrent_writes._GATES[id(client)] is first
    assert first.limit <= max(learned, 3)


def test_client_without_counter_uses_fixed_writers(monkeypatch):
    monkeypatch.setattr(concurrent_writes, "_GATES", {})

    class _Plain:
        def update_record(self, *a) -> None:
            return None

    assert write_records(_Plain(), "inventoryItem", [("1", {})], workers=4) == (1, 0)
    assert concurrent_writes._GATES == {}
    assert concurrent_writes.throttle_report() is None


def test_leg_deadline(monkeypatch):
    monkeypatch.delenv("LEG_DEADLINE_EPOCH", raising=False)
    assert run_status.leg_deadline() is None
    assert run_status.past_deadline(None) is False
    monkeypatch.setenv("LEG_DEADLINE_EPOCH", "100")
    assert run_status.leg_deadline() == 100.0
    assert run_status.past_deadline(100.0) is True
    monkeypatch.setenv("LEG_DEADLINE_EPOCH", "not a number")
    assert run_status.leg_deadline() is None


def test_client_counts_429s_per_thread(monkeypatch):
    import dataclasses

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from sanmar_netsuite.netsuite.client import NetSuiteClient
    from test_production_guard import _config

    class _Resp:
        reason = "x"
        headers: dict = {}
        text = "{}"
        content = b"{}"

        def __init__(self, status: int) -> None:
            self.status_code = status

        def json(self):
            return {}

    class _Session:
        def __init__(self) -> None:
            self.answers = [429, 429, 200]

        def request(self, method, url, **_kw):
            return _Resp(self.answers.pop(0))

    cfg = dataclasses.replace(_config("1234567", allow_prod=True), read_only=False)
    client = NetSuiteClient(cfg)
    client._session = _Session()
    monkeypatch.setattr(NetSuiteClient._request.retry, "sleep", lambda _s: None)
    assert client.throttle_count() == 0
    client._request("PATCH", "https://x/services/rest/record/v1/inventoryItem/1",
                    json_body={})
    assert client.throttle_count() == 2
    seen: list[int] = []
    t = threading.Thread(target=lambda: seen.append(client.throttle_count()))
    t.start()
    t.join()
    assert seen == [0]

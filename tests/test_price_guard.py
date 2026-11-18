"""The price guard: hold a vendor's prices when too many move too far.

Andy, 2026-11-12: hold any price list where more than a quarter of the prices
move by more than 25%.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import native_pricing  # noqa: E402
import price_guard  # noqa: E402
import vendor_pipeline as vp  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    price_guard.reset()
    monkeypatch.delenv("PRICE_GUARD_HOLD", raising=False)
    vp._SUMMARY.clear()
    yield
    price_guard.reset()
    vp._SUMMARY.clear()


def _same(cur, new):
    try:
        return float(cur) == float(new)
    except (TypeError, ValueError):
        return False


def test_small_move_and_big_move_are_counted_apart():
    price_guard.record("price", "10.00", 12.00)   # 20%: not big
    price_guard.record("price", "10.00", 12.60)   # 26%: big
    price_guard.record("price", "10.00", 7.40)    # down 26%: big
    price_guard.record("price", "10.00", 10.00)   # unchanged
    s = price_guard.snapshot()["price"]
    assert s == {"compared": 4, "moved_big": 2, "filled_blank": 0}


def test_filling_a_blank_price_is_not_a_move():
    for cur in (None, "", "0", "0.00"):
        price_guard.record("cost", cur, 5.0)
    s = price_guard.snapshot()["cost"]
    assert s == {"compared": 0, "moved_big": 0, "filled_blank": 4}


def test_trips_only_above_a_quarter():
    exactly = {"price": {"compared": 100, "moved_big": 25}, "cost": {}}
    over = {"price": {"compared": 100, "moved_big": 26}, "cost": {}}
    assert price_guard.tripped(exactly) == []
    assert price_guard.tripped(over) == ["price"]


def test_cost_trips_on_its_own():
    r = {"price": {"compared": 100, "moved_big": 0},
         "cost": {"compared": 40, "moved_big": 30}}
    assert price_guard.tripped(r) == ["cost"]


def test_a_handful_of_prices_never_trips():
    r = {"price": {"compared": 5, "moved_big": 5}, "cost": {}}
    assert price_guard.tripped(r) == []


def test_add_native_diffs_writes_when_not_holding():
    body: dict = {}
    native_pricing.add_native_diffs(
        body, {"cost": "3.00"}, {"7": "10.00"}, "7",
        price=20.0, cost=4.0, weight=None, same=_same)
    assert body["cost"] == 4.0 and "price" in body
    snap = price_guard.snapshot()
    assert snap["price"]["moved_big"] == 1 and snap["cost"]["moved_big"] == 1


def test_add_native_diffs_holds_price_and_cost_but_not_weight(monkeypatch):
    monkeypatch.setenv("PRICE_GUARD_HOLD", "1")
    body: dict = {}
    native_pricing.add_native_diffs(
        body, {"cost": "3.00", "weight": "1"}, {"7": "10.00"}, "7",
        price=20.0, cost=4.0, weight=2.0, same=_same)
    assert "price" not in body and "cost" not in body
    assert body["weight"] == 2.0


def _fake_run(report, calls):
    def run(script, env, *, label="", summarize=True):
        calls.append(dict(env))
        path = env.get("PRICE_GUARD_REPORT")
        if path and report is not None:
            Path(path).write_text(json.dumps(report))
        return 0
    return run


BAD = {"price": {"compared": 100, "moved_big": 60, "filled_blank": 0},
       "cost": {"compared": 100, "moved_big": 0, "filled_blank": 0}}
GOOD = {"price": {"compared": 100, "moved_big": 3, "filled_blank": 2},
        "cost": {"compared": 100, "moved_big": 1, "filled_blank": 0}}


def test_guard_off_is_one_plain_run(monkeypatch):
    calls: list = []
    monkeypatch.setattr(vp, "_run", _fake_run(BAD, calls))
    vp._run_update("x.py", {"SYNC_DRY_RUN": "false"})
    assert len(calls) == 1 and "PRICE_GUARD_REPORT" not in calls[0]


def test_live_run_checks_first_then_holds_a_bad_list(monkeypatch):
    calls: list = []
    monkeypatch.setattr(vp, "_run", _fake_run(BAD, calls))
    vp._run_update("x.py", {"SYNC_DRY_RUN": "false", "PRICE_GUARD": "on"})
    check, live = calls
    assert check["SYNC_DRY_RUN"] == "true" and check["NETSUITE_READ_ONLY"] == "true"
    assert live["SYNC_DRY_RUN"] == "false" and live["PRICE_GUARD_HOLD"] == "1"
    assert any("PRICE HOLD" in x for _, lines in vp._SUMMARY for x in lines)


def test_live_run_writes_prices_for_a_normal_list(monkeypatch):
    calls: list = []
    monkeypatch.setattr(vp, "_run", _fake_run(GOOD, calls))
    vp._run_update("x.py", {"SYNC_DRY_RUN": "false", "PRICE_GUARD": "on"})
    assert "PRICE_GUARD_HOLD" not in calls[1]


def test_override_releases_a_held_list(monkeypatch):
    calls: list = []
    monkeypatch.setattr(vp, "_run", _fake_run(BAD, calls))
    vp._run_update("x.py", {"SYNC_DRY_RUN": "false", "PRICE_GUARD": "on",
                            "PRICE_GUARD_OVERRIDE": "RELEASE"})
    assert "PRICE_GUARD_HOLD" not in calls[1]


def test_no_report_holds_prices(monkeypatch):
    calls: list = []
    monkeypatch.setattr(vp, "_run", _fake_run(None, calls))
    vp._run_update("x.py", {"SYNC_DRY_RUN": "false", "PRICE_GUARD": "on"})
    assert calls[1]["PRICE_GUARD_HOLD"] == "1"


def test_dry_run_is_one_pass_and_reports(monkeypatch):
    calls: list = []
    monkeypatch.setattr(vp, "_run", _fake_run(BAD, calls))
    vp._run_update("x.py", {"SYNC_DRY_RUN": "true", "PRICE_GUARD": "on"})
    assert len(calls) == 1
    assert any("PRICE HOLD" in x for _, lines in vp._SUMMARY for x in lines)


def test_run_keeps_totals_and_skips_for_the_summary(tmp_path, monkeypatch):
    script = tmp_path / "s.py"
    script.write_text(
        "print('Retryable NetSuite response 429 for PATCH x')\n"
        "print('  CHILD SKIPPED A-B-C: an item with that name already exists')\n"
        "print('chatter')\n"
        "print('x update: wrote 3 item(s); unchanged: 9; failures: 0')\n")
    monkeypatch.setattr(vp, "ROOT", tmp_path)
    assert vp._run("s.py", {"PATH": "/usr/bin:/bin"}) == 0
    (_, lines), = vp._SUMMARY
    assert lines == [
        "CHILD SKIPPED A-B-C: an item with that name already exists",
        "x update: wrote 3 item(s); unchanged: 9; failures: 0",
    ]


def test_summary_is_written_to_the_file(tmp_path, monkeypatch):
    out = tmp_path / "sum.md"
    monkeypatch.setenv("RUN_SUMMARY_FILE", str(out))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setenv("SYNC_DRY_RUN", "true")
    vp._SUMMARY.append(("a.py", ["a: wrote 1 item(s); failures: 0"]))
    vp.write_summary("sanmar", ("update",), 0)
    text = out.read_text()
    assert "sanmar: update (dry run (nothing written))" in text
    assert "- a: wrote 1 item(s); failures: 0" in text

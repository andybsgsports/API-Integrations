"""Vendor line Purchase Price follows the item's cost (Andy, 2026-11-18)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_status  # noqa: E402
import vendor_line_price_sync as vl  # noqa: E402
import vendor_pipeline as vp  # noqa: E402


def _line(vendor, preferred, price=None, code="DT2500"):
    return {"vendor": vendor, "preferredvendor": "T" if preferred else "F",
            "purchaseprice": price, "vendorcode": code}


def test_blank_preferred_line_is_filled_from_cost():
    assert vl.synced_lines([_line(512, True)], "3.49") == [
        {"vendor": {"id": "512"}, "preferredVendor": True,
         "vendorCode": "DT2500", "purchasePrice": 3.49}]


def test_a_preferred_line_that_differs_is_moved_to_cost():
    lines = vl.synced_lines([_line(512, True, price="2.99")], 3.49)
    assert lines[0]["purchasePrice"] == 3.49


def test_a_matching_line_is_left_alone():
    assert vl.synced_lines([_line(512, True, price="3.49")], 3.49) is None
    assert vl.synced_lines([_line(512, True, price="3.490")], "3.49") is None


def test_other_vendor_lines_keep_their_own_price():
    lines = vl.synced_lines(
        [_line(512, True, price="2.99"), _line(510, False, price="4.10", code="X")],
        3.49)
    assert lines[1] == {"vendor": {"id": "510"}, "preferredVendor": False,
                        "vendorCode": "X", "purchasePrice": 4.10}


def test_a_non_preferred_blank_line_is_not_filled():
    lines = vl.synced_lines(
        [_line(512, True, price="2.99"), _line(510, False)], 3.49)
    assert "purchasePrice" not in lines[1]


def test_no_cost_means_no_change():
    for cost in (None, "", 0, "0.00"):
        assert vl.synced_lines([_line(512, True)], cost) is None


def test_differing_lines_go_before_blank_ones_under_the_cap():
    picked = vl.pick([("1", True), ("2", False), ("3", True), ("4", False)], 3)
    assert picked == ["2", "4", "1"]


def test_every_pricing_vendor_runs_the_sync_after_its_update():
    for vendor in ("sanmar", "momentec", "ua", "ss"):
        update = vp.PIPELINES[vendor]["update"]
        assert update[-1] == "scripts/vendor_line_price_sync.py", vendor
        assert vp.VENDOR_ENV[vendor]["VENDOR_LINE_SOURCE"] == vendor


def test_rotated_covers_everything_and_moves_each_day():
    a, b = run_status.rotated(10, 100), run_status.rotated(10, 101)
    assert sorted(a) == list(range(10)) and sorted(b) == list(range(10))
    assert a[0] != b[0]
    assert run_status.rotated(0, 5) == []
    assert run_status.rotated(1, 5) == [0]


def test_field_counts_line(capsys):
    run_status.print_field_counts("x", {"cost": 2, "custitem_qty": 40})
    assert capsys.readouterr().out.strip() == (
        "NOTE: x fields changed: custitem_qty 40, cost 2")


def test_a_price_hold_reaches_the_sync_that_follows(monkeypatch):
    seen: list[tuple[str, str]] = []

    def fake(script, env, **kw):
        seen.append((script, env.get("PRICE_GUARD_HOLD", "")))
        path = env.get("PRICE_GUARD_REPORT")
        if path:
            Path(path).write_text(
                '{"price": {"compared": 100, "moved_big": 90, "filled_blank": 0},'
                ' "cost": {"compared": 0, "moved_big": 0, "filled_blank": 0}}')
        return 0

    monkeypatch.setattr(vp, "_run", fake)
    monkeypatch.setenv("PRICE_GUARD", "on")
    monkeypatch.setenv("SYNC_DRY_RUN", "false")
    monkeypatch.delenv("PRICE_GUARD_HOLD", raising=False)
    vp.run_vendor("momentec", phases=("update",))
    vp._SUMMARY.clear()
    assert seen[-1] == ("scripts/vendor_line_price_sync.py", "1")


class _Cfg:
    matrix_script_id = "s"
    matrix_deploy_id = "d"


class _Restlet:
    def __init__(self, supports=True, fail_ids=()):
        self.supports = supports
        self.fail_ids = set(fail_ids)
        self.calls = []

    def call_restlet(self, script_id, deploy_id, body):
        self.calls.append(body)
        if not self.supports:
            return {"results": []}
        return {"action": "vendorPrices", "results": [
            {"id": it["id"], "status": "error", "message": "found 2"}
            if it["id"] in self.fail_ids else {"id": it["id"], "status": "updated"}
            for it in body["vendorPrices"]]}


def _priced(n):
    return [{"id": i, "vendor": 576, "price": 44.0} for i in range(1, n + 1)]


def test_restlet_prices_batches_and_counts():
    client = _Restlet(fail_ids={7})
    out = vl.restlet_prices(client, _Cfg(), _priced(60), None, "x")
    assert out == (59, 1, 1, 0)
    assert [len(c["vendorPrices"]) for c in client.calls] == [25, 25, 10]
    assert all(c["action"] == "vendorPrices" and "items" not in c for c in client.calls)


def test_restlet_prices_older_restlet_is_not_a_failure(capsys):
    client = _Restlet(supports=False)
    assert vl.restlet_prices(client, _Cfg(), _priced(60), None, "x") == (0, 0, 0, 60)
    assert len(client.calls) == 1
    assert "does not have the vendorPrices action" in capsys.readouterr().out


def test_restlet_prices_not_configured():
    class _None:
        matrix_script_id = ""
        matrix_deploy_id = ""

    client = _Restlet()
    assert vl.restlet_prices(client, _None(), _priced(3), None, "x") == (0, 0, 0, 3)
    assert client.calls == []


def test_restlet_prices_stops_at_the_deadline():
    client = _Restlet()
    out = vl.restlet_prices(client, _Cfg(), _priced(60), 1.0, "x")
    assert out == (25, 0, 0, 35)

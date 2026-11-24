"""Base Price line keyed at quantity 1 (production J317 / L317, 2026-11-23)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import concurrent_writes as cw  # noqa: E402

ERR = ("NetSuite 404: Not Found :: {'o:errorDetails': [{'detail': 'Unable to "
       "find the specified sublist line.', 'o:errorCode': 'NONEXISTENT_SUBLIST_KEY'}]}")


def _body(q=0):
    return {"weight": 0.4, "price": {"items": [{
        "currencyPage": 1, "priceLevel": {"id": 1},
        "quantity": {"value": q}, "price": 42.62}]}}


class Client:
    def __init__(self, fail_q0=True, fail_all=False):
        self.calls, self.fail_q0, self.fail_all = [], fail_q0, fail_all

    def update_record(self, record_type, rid, body):
        self.calls.append(body)
        q = body["price"]["items"][0]["quantity"]["value"] if "price" in body else None
        if self.fail_all or (self.fail_q0 and q == 0):
            raise RuntimeError(ERR)


def test_retries_at_quantity_one_and_keeps_other_fields():
    cw.PRICE_LINE_RETRIES["count"] = 0
    c = Client()
    assert cw.write_records(c, "inventoryItem", [("1", _body())], workers=1) == (1, 0)
    assert c.calls[1]["price"]["items"][0]["quantity"] == {"value": 1}
    assert c.calls[1]["price"]["items"][0]["price"] == 42.62
    assert c.calls[1]["weight"] == 0.4
    assert cw.PRICE_LINE_RETRIES["count"] == 1


def test_still_a_failure_when_quantity_one_also_fails():
    seen = []
    c = Client(fail_all=True)
    out = cw.write_records(c, "inventoryItem", [("1", _body())], workers=1,
                           on_error=lambda rid, exc: seen.append(rid))
    assert out == (0, 1) and seen == ["1"] and len(c.calls) == 2


def test_no_retry_without_a_price_line_or_for_other_errors():
    assert cw._price_line_at_quantity_one({"weight": 1}, RuntimeError(ERR)) is None
    assert cw._price_line_at_quantity_one(_body(), RuntimeError("429")) is None
    assert cw._price_line_at_quantity_one(_body(1), RuntimeError(ERR)) is None

"""Unit tests for sale-aware Purchase Price (scripts/ss_backfill.effective_cost).

S&S publishes ``salePrice`` only while a promotion is live (no start/end window
in the feed), so cost tracks the sale price whenever it undercuts our regular
cost and reverts to that regular cost otherwise.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ss_backfill import effective_cost, natives_for  # noqa: E402


def test_active_sale_uses_sale_price():
    assert effective_cost(Decimal("6.50"), Decimal("4.99")) == (4.99, True)


def test_no_sale_price_uses_regular():
    assert effective_cost(Decimal("6.50"), None) == (6.50, False)


def test_sale_not_lower_is_ignored():
    # sale price >= regular, or the 0.00 placeholder -> not a genuine discount
    assert effective_cost(Decimal("6.50"), Decimal("6.50")) == (6.50, False)
    assert effective_cost(Decimal("6.50"), Decimal("7.25")) == (6.50, False)
    assert effective_cost(Decimal("6.50"), Decimal("0")) == (6.50, False)


def test_no_regular_uses_sale_without_flag():
    # nothing to compare against -> use the sale value as cost, don't flag it
    assert effective_cost(None, Decimal("4.99")) == (4.99, False)
    assert effective_cost(None, None) == (None, False)


def test_natives_prefers_customer_price_as_regular():
    # customer (program) price is our regular cost; sale below it -> on sale.
    # shipping weight stays in pounds -- no oz conversion.
    p = {"msrp": "9.00", "customer_price": "6.50", "piece_price": "7.75",
         "sale_price": "4.99", "weight": "0.5"}
    base, cost, weight, weight_unit, on_sale = natives_for(p)
    assert (base, cost, weight, weight_unit, on_sale) == (9.0, 4.99, 0.5, {"id": "1"}, True)


def test_natives_falls_back_to_piece_price():
    # no customer price -> piece price is the regular; no sale -> cost = piece
    p = {"msrp": "9.00", "piece_price": "7.75", "weight": "0.5"}
    base, cost, weight, weight_unit, on_sale = natives_for(p)
    assert (base, cost, weight, weight_unit, on_sale) == (9.0, 7.75, 0.5, {"id": "1"}, False)


def test_natives_sale_between_customer_and_piece_not_flagged():
    # sale (7.00) is below list piece (7.75) but ABOVE our customer price (6.50):
    # not a discount to us, so cost stays at customer price and on_sale is False
    p = {"customer_price": "6.50", "piece_price": "7.75", "sale_price": "7.00"}
    _base, cost, _weight, _weight_unit, on_sale = natives_for(p)
    assert (cost, on_sale) == (6.50, False)

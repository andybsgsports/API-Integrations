"""Unit tests for sale-aware Purchase Price (scripts/sanmar_field_update.effective_cost).

Cost tracks the active sale price while a genuine, in-window sale runs, and
reverts to the regular piece price otherwise.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sanmar_field_update import effective_cost  # noqa: E402

TODAY = date(2026, 7, 21)


def test_active_sale_uses_sale_price():
    cost, on_sale = effective_cost(
        Decimal("3.11"), Decimal("2.39"), "07/01/2026", "07/31/2026", TODAY
    )
    assert (cost, on_sale) == (2.39, True)


def test_no_dates_but_lower_price_is_on_sale():
    cost, on_sale = effective_cost(Decimal("3.54"), Decimal("2.89"), "", "", TODAY)
    assert (cost, on_sale) == (2.89, True)


def test_sale_window_not_started_uses_regular():
    cost, on_sale = effective_cost(
        Decimal("3.11"), Decimal("2.39"), "08/01/2026", "08/31/2026", TODAY
    )
    assert (cost, on_sale) == (3.11, False)


def test_expired_sale_reverts_to_regular():
    cost, on_sale = effective_cost(
        Decimal("3.11"), Decimal("2.39"), "06/01/2026", "06/30/2026", TODAY
    )
    assert (cost, on_sale) == (3.11, False)


def test_sale_not_actually_lower_is_ignored():
    # sale price >= regular, or the 0.00 placeholder -> not a real discount
    assert effective_cost(Decimal("3.11"), Decimal("3.11"), "", "", TODAY) == (3.11, False)
    assert effective_cost(Decimal("3.11"), Decimal("0"), "", "", TODAY) == (3.11, False)


def test_no_sale_price_uses_regular():
    assert effective_cost(Decimal("3.11"), None, "", "", TODAY) == (3.11, False)

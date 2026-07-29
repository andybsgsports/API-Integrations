"""Unit tests for the pure helpers inside scripts/ (the CI writers).

The writers themselves need live NetSuite/supplier access, but their
transformation helpers are pure functions — this pins the behavior the live
runs have already validated.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from description_update import _copy, _polish_name  # noqa: E402
from native_pricing import add_native_diffs, base_price_body, weight_display  # noqa: E402
from parent_sync import _mode  # noqa: E402
from ss_backfill import _abs_url, natives_for  # noqa: E402


def _same(cur, new) -> bool:
    cs = ("" if cur is None else str(cur)).strip()
    try:
        return float(cs) == float(str(new))
    except ValueError:
        return cs == str(new)


class TestPolishName:
    def test_strips_status_prefix_and_style_suffix(self):
        assert (
            _polish_name("DISCONTINUED Port & Co Fan Favorite Tee. PC450", "PC450")
            == "Port & Co Fan Favorite Tee"
        )

    def test_title_cases_all_caps(self):
        assert _polish_name("HEAVY BLEND HOODIE", "") == "Heavy Blend Hoodie"

    def test_leaves_mixed_case_alone(self):
        assert _polish_name("BadgerBlend Tee", "") == "BadgerBlend Tee"


class TestCopy:
    def test_plain_title_on_all_three_fields(self):
        out = _copy("Fit Flex Tee", "", "100000", "Black", "2XL", "marketing copy")
        assert out == {
            "displayName": "Fit Flex Tee",
            "salesDescription": "Fit Flex Tee",
            "purchaseDescription": "Fit Flex Tee",
        }

    def test_empty_name_yields_no_fields(self):
        assert _copy("", "", "", "", "", "desc") == {}


class TestMode:
    def test_most_common_wins(self):
        assert _mode(["a", "a", "b"]) == "a"

    def test_ignores_empties(self):
        assert _mode(["", "", "x"]) == "x"

    def test_all_empty_is_none(self):
        assert _mode(["", ""]) is None
        assert _mode([]) is None


class TestSsHelpers:
    def test_relative_cdn_path_becomes_absolute(self):
        assert (
            _abs_url("Images/Color/x.jpg")
            == "https://cdn.ssactivewear.com/Images/Color/x.jpg"
        )

    def test_absolute_url_passes_through(self):
        assert _abs_url("https://a/b.jpg") == "https://a/b.jpg"

    def test_empty_is_none(self):
        assert _abs_url("") is None
        assert _abs_url(None) is None

    def test_natives_prefers_customer_price(self):
        # no sale price -> cost is the customer price; on_sale False.
        # shipping weight always stays in pounds (no oz conversion).
        assert natives_for(
            {"msrp": "28", "customer_price": "11.5", "piece_price": "12", "weight": "0.4"}
        ) == (28.0, 11.5, 0.4, {"id": "1"}, False)

    def test_natives_falls_back_to_piece_price(self):
        assert natives_for({"piece_price": "12"}) == (None, 12.0, None, None, False)


class TestNativeDiffs:
    def test_writes_only_differing_fields(self):
        body: dict = {}
        add_native_diffs(
            body,
            {"cost": "11.2", "weight": None},
            {"1": "20"},
            "1",
            price=28.0,
            cost=11.2,
            weight=0.5,
            same=_same,
        )
        assert body["price"] == base_price_body(28.0)
        assert "cost" not in body  # already 11.2
        assert body["weight"] == 0.5

    def test_all_matching_writes_nothing(self):
        body: dict = {}
        add_native_diffs(
            body,
            {"cost": "11.2", "weight": "0.5"},
            {"1": "28.0"},
            "1",
            price=28.0,
            cost=11.2,
            weight=0.5,
            same=_same,
        )
        assert body == {}

    def test_none_values_skipped(self):
        body: dict = {}
        add_native_diffs(
            body, {"cost": None, "weight": None}, {}, "1",
            price=None, cost=None, weight=None, same=_same,
        )
        assert body == {}

    def test_weight_unit_written_when_it_differs(self):
        body: dict = {}
        add_native_diffs(
            body, {"cost": "11.2", "weight": "0.5", "weightunit": "1"}, {}, "1",
            price=None, cost=11.2, weight=0.5, weight_unit={"id": "2"}, same=_same,
        )
        assert body == {"weightUnit": {"id": "2"}}  # weight itself unchanged (0.5 == 0.5)

    def test_weight_unit_unchanged_writes_nothing(self):
        body: dict = {}
        add_native_diffs(
            body, {"cost": "11.2", "weight": "0.5", "weightunit": "1"}, {}, "1",
            price=None, cost=11.2, weight=0.5, weight_unit={"id": "1"}, same=_same,
        )
        assert body == {}

    def test_weight_and_unit_written_together(self):
        # When both the number and the unit differ from the row, both land in
        # the same body (mechanics test -- units are arbitrary here).
        body: dict = {}
        add_native_diffs(
            body, {"cost": None, "weight": "0.3", "weightunit": "1"}, {}, "1",
            price=None, cost=None, weight=4.8, weight_unit={"id": "2"}, same=_same,
        )
        assert body == {"weight": 4.8, "weightUnit": {"id": "2"}}


class TestWeightDisplay:
    def test_light_item_stays_pounds(self):
        # Shipping weight is kept in pounds across the catalogue -- no oz
        # conversion for sub-1-lb items (business decision 2026-07-29).
        assert weight_display(0.3) == (0.3, {"id": "1"})

    def test_heavy_item_stays_pounds(self):
        assert weight_display(1.15) == (1.15, {"id": "1"})

    def test_none_returns_none_none(self):
        assert weight_display(None) == (None, None)


class TestPerWarehouseFields:
    """ss_backfill.payload_for's per-warehouse columns (warehouse_fields maps)."""

    def _product(self) -> dict:
        return {"sku": "B0001", "style_name": "G500", "qty_available": 7}

    def test_no_inventory_rows_leaves_columns_unset(self):
        from ss_backfill import payload_for
        from warehouse_fields import SS_QTY_FIELDS

        want = payload_for(self._product())
        assert not any(f in want for f in SS_QTY_FIELDS)
        # the retired text-blob field must never be written again
        assert "custitem_ss_qty_by_whse" not in want

    def test_rows_fill_matched_columns_and_zero_fill_rest(self):
        from ss_backfill import payload_for
        from warehouse_fields import SS_QTY_FIELDS, SS_WHSE_FIELDS

        rows = [
            {"warehouseAbbr": "IL", "qty": 12},
            {"warehouseAbbr": "TX", "qty": 0},
        ]
        want = payload_for(self._product(), rows)
        assert want[SS_WHSE_FIELDS["IL"][0]] == 12
        assert want[SS_WHSE_FIELDS["TX"][0]] == 0
        # every other column zero-filled, so stale counts always clear
        assert all(want[f] == 0 for f in SS_QTY_FIELDS
                   if f not in (SS_WHSE_FIELDS["IL"][0],))
        # the retired text-blob field must never be written again
        assert "custitem_ss_qty_by_whse" not in want

    def test_unknown_code_collected_not_dropped_silently(self):
        import ss_backfill
        from ss_backfill import payload_for

        ss_backfill.UNKNOWN_WHSE.clear()
        payload_for(self._product(), [{"warehouseAbbr": "ZZ", "qty": 5}])
        assert "ZZ" in ss_backfill.UNKNOWN_WHSE

    def test_sanmar_map_covers_constants_table(self):
        from warehouse_fields import SANMAR_WHSE_FIELDS

        from sanmar_netsuite.sanmar import constants as C

        assert set(SANMAR_WHSE_FIELDS) == set(C.WAREHOUSES)

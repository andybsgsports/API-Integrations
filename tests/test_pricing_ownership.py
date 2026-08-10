"""Preferred-Vendor pricing ownership (scripts/pricing_ownership.py).

The item's Preferred Vendor decides which nightly feed may write the native
price/cost/weight fields; items without one fall back to vendor_sublist.py's
ranking among the feeds that actually match the item. feed_seen.stamp grew a
claim_source flag so a non-owning feed keeps the lifecycle heartbeat fresh
without flipping custitem_feed_source every night.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pricing_ownership import (  # noqa: E402
    VENDOR_MOMENTEC,
    VENDOR_SANMAR,
    VENDOR_SS,
    VENDOR_UA,
    owns_pricing,
    read_preferred,
)

from sanmar_netsuite.netsuite.feed_seen import stamp  # noqa: E402

# --- owns_pricing: Preferred Vendor set -> ownership follows it exactly

def test_preferred_vendor_wins():
    assert owns_pricing(VENDOR_SANMAR, VENDOR_SANMAR, {})
    assert owns_pricing(VENDOR_SS, VENDOR_SS, {})
    assert not owns_pricing(VENDOR_SS, VENDOR_SANMAR, {})
    assert not owns_pricing(VENDOR_SANMAR, VENDOR_SS, {})


def test_preferred_vendor_outside_ranking_means_nobody_owns():
    # Andy points Preferred at a non-feed vendor -> every feed defers and the
    # item's pricing stays manually managed.
    assert not owns_pricing(VENDOR_SANMAR, 999, {})
    assert not owns_pricing(VENDOR_SS, 999, {})


def test_preferred_vendor_overrides_ranking_fallback():
    # S&S preferred beats SanMar even though the row shows a SanMar match.
    row = {"custitem_sanmar_style": "PC61"}
    assert owns_pricing(VENDOR_SS, VENDOR_SS, row)
    assert not owns_pricing(VENDOR_SANMAR, VENDOR_SS, row)


# --- owns_pricing: no Preferred Vendor -> ranking among matched feeds

def test_fallback_sanmar_always_owns():
    # Top-ranked: owns even when every other feed matches the item too.
    row = {"custitem_ss_sku": "B00760003", "custitem_mtec_item_sku": "029.BLK.L"}
    assert owns_pricing(VENDOR_SANMAR, None, row)


def test_fallback_ss_defers_to_higher_ranked_matches():
    assert owns_pricing(VENDOR_SS, None, {})
    assert not owns_pricing(VENDOR_SS, None, {"custitem_sanmar_style": "PC61"})
    assert not owns_pricing(VENDOR_SS, None, {"custitem_mtec_item_sku": "029.BLK.L"})
    # Blank/whitespace key fields don't count as a match.
    assert owns_pricing(VENDOR_SS, None, {"custitem_sanmar_style": "  "})


def test_fallback_momentec_defers_only_to_sanmar():
    assert owns_pricing(VENDOR_MOMENTEC, None, {"custitem_ss_sku": "B00760003"})
    assert not owns_pricing(
        VENDOR_MOMENTEC, None, {"custitem_sanmar_style": "PC61"}
    )


def test_fallback_ua_is_lowest_ranked():
    assert owns_pricing(VENDOR_UA, None, {})
    for field in ("custitem_sanmar_style", "custitem_mtec_item_sku",
                  "custitem_ss_sku"):
        assert not owns_pricing(VENDOR_UA, None, {field: "X"})


# --- read_preferred: tolerant of throttling

class _Client:
    def __init__(self, rows=None, raise_exc=False):
        self.rows = rows or []
        self.raise_exc = raise_exc
        self.queries: list[str] = []

    def suiteql(self, q):
        self.queries.append(q)
        if self.raise_exc:
            raise RuntimeError("429 Too Many Requests")
        return self.rows


def test_read_preferred_maps_item_to_vendor():
    client = _Client(rows=[
        {"item": 132749, "vendor": "512"},
        {"item": "205001", "vendor": 510},
    ])
    assert read_preferred(client, "'132749', '205001'") == {
        "132749": 512, "205001": 510,
    }
    assert "preferredvendor = 'T'" in client.queries[0]


def test_read_preferred_failure_returns_empty(capsys):
    # A throttled read falls back to the ranking for one chunk instead of
    # crashing the run.
    assert read_preferred(_Client(raise_exc=True), "'1'") == {}
    assert "ranking fallback" in capsys.readouterr().out


# --- feed_seen.stamp: claim_source

TODAY = date.today().isoformat()
FRESH = date.today() - timedelta(days=1)


def test_stamp_owner_claims_source():
    want: dict = {}
    stamp(want, {"custitem_feed_source": "ss",
                 "custitem_feed_last_seen": FRESH.strftime("%m/%d/%Y")}, "sanmar")
    assert want == {"custitem_feed_source": "sanmar"}


def test_stamp_non_owner_leaves_live_source_alone():
    # The nightly ping-pong: S&S must stop re-stamping 'ss' over the owner's
    # 'sanmar' -- but still refresh a stale heartbeat.
    stale = (date.today() - timedelta(days=4)).strftime("%m/%d/%Y")
    want: dict = {}
    stamp(want, {"custitem_feed_source": "sanmar",
                 "custitem_feed_last_seen": stale}, "ss", claim_source=False)
    assert want == {"custitem_feed_last_seen": TODAY}


def test_stamp_non_owner_fills_blank_source():
    want: dict = {}
    stamp(want, {"custitem_feed_source": "",
                 "custitem_feed_last_seen": ""}, "ss", claim_source=False)
    assert want == {"custitem_feed_source": "ss",
                    "custitem_feed_last_seen": TODAY}


def test_stamp_fresh_heartbeat_untouched():
    want: dict = {}
    stamp(want, {"custitem_feed_source": "sanmar",
                 "custitem_feed_last_seen": FRESH.strftime("%m/%d/%Y")},
          "sanmar")
    assert want == {}

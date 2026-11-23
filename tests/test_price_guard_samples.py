"""Price guard: the review list of moves and the log samples."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import price_guard as pg  # noqa: E402


def test_moves_and_samples(tmp_path, monkeypatch):
    pg.reset()
    monkeypatch.setattr(pg, "MOVES_FILE", tmp_path / "moves.csv")
    pg.record("price", "10", "20", ref="1", name="A-Black-S")   # +100%
    pg.record("price", "10", "10", ref="2", name="unchanged")
    pg.record("price", None, "15", ref="3", name="blank")
    pg.record("cost", "8", "4", ref="4", name="B-Red-M")        # -50%
    assert pg.snapshot()["price"] == {
        "compared": 2, "moved_big": 1, "filled_blank": 1}
    lines = pg.sample_lines()
    assert lines == [
        "PRICE GUARD sample (Base Price, largest up): "
        "item 1 A-Black-S: 10.00 -> 20.00 (+100%)",
        "PRICE GUARD sample (Purchase Price, largest down): "
        "item 4 B-Red-M: 8.00 -> 4.00 (-50%)",
    ]
    pg._write_moves()
    text = (tmp_path / "moves.csv").read_text().splitlines()
    assert text[0].startswith("Field,NetSuite Item Id")
    assert len(text) == 4 and "unchanged" not in "".join(text)
    assert "Base Price,3,blank,,15.00,," in text
    pg.reset()
    assert pg._moves == []


def test_base_price_is_not_lowered_when_rule_is_on(monkeypatch):
    import native_pricing as np_

    def same(a, b):
        return a not in (None, "") and abs(float(a) - float(b)) < 0.005

    pg.reset()
    monkeypatch.setenv("BASE_PRICE_NO_DECREASE", "on")
    body: dict = {}
    np_.add_native_diffs(body, {"itemid": "5000-Black-S", "cost": "3"},
                         {"1": "33.00"}, "1", price=6.22, cost=None,
                         weight=None, same=same)
    assert "price" not in body
    assert pg.snapshot()["price"] == {
        "compared": 0, "moved_big": 0, "filled_blank": 0}
    assert pg._moves == [("kept", "1", "5000-Black-S", 33.0, 6.22)]
    # Higher and blank still write.
    for rid, cur in (("2", "5.00"), ("3", "")):
        b: dict = {}
        np_.add_native_diffs(b, {"itemid": "x"}, {rid: cur}, rid, price=6.22,
                             cost=None, weight=None, same=same)
        assert "price" in b
    # Rule off: a lower price writes as before.
    monkeypatch.delenv("BASE_PRICE_NO_DECREASE")
    b = {}
    np_.add_native_diffs(b, {"itemid": "x"}, {"4": "33.00"}, "4", price=6.22,
                         cost=None, weight=None, same=same)
    assert "price" in b
    pg.reset()

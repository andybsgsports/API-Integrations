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

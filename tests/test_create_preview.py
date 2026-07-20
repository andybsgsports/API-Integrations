"""Unit tests for the SanMar create-preview bucketing (scripts/sanmar_create_preview).

The live run needs SanMar SFTP + NetSuite, but the diff bucketing is a pure
function: given the feed styles, the resolved parents, and the already-present
combos, split what's missing into "new child under an existing parent" (import
now) vs "child of a net-new parent style" (needs the parent first).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sanmar_create_preview import split_csv, split_missing  # noqa: E402


def _sku(key, color, size):
    return SimpleNamespace(unique_key=key, color_name=color, size=size)


def _style(style, skus):
    return SimpleNamespace(style=style, skus=skus)


def test_split_separates_existing_parent_from_net_new_parent():
    styles = [
        # K420 exists as a parent; Black/S already present, Navy/S is new.
        _style("K420", [_sku("A", "Black", "S"), _sku("B", "Navy", "S")]),
        # 9999 has no parent record yet -> all its rows are net-new-parent.
        _style("9999", [_sku("C", "Red", "S"), _sku("D", "Red", "M")]),
    ]
    parent_refs = {"K420": "50286"}
    skip = {"SANMAR-A"}  # Black/S already in NetSuite

    out = split_missing(styles, parent_refs, skip)

    assert out.new_children == 1          # K420 Navy/S
    assert out.new_parent_rows == 2       # both 9999 rows
    assert out.new_parent_styles == {"9999"}
    # child CSV must exclude the already-present combo AND every net-new-parent row
    assert out.child_only_skip == {"SANMAR-A", "SANMAR-C", "SANMAR-D"}


def test_split_all_in_sync_when_everything_skipped():
    styles = [_style("K420", [_sku("A", "Black", "S")])]
    out = split_missing(styles, {"K420": "1"}, {"SANMAR-A"})
    assert out.new_children == 0
    assert out.new_parent_rows == 0
    assert out.new_parent_styles == set()


def test_split_csv_chunks_under_limit_with_header_on_each(tmp_path):
    src = tmp_path / "big.csv"
    lines = ["External ID,Name\n"] + [f"SANMAR-{i},{i}\n" for i in range(2500)]
    src.write_text("".join(lines), encoding="utf-8")

    parts = split_csv(src, tmp_path, "big", 1000)  # 2500 rows -> 3 parts

    assert len(parts) == 3
    seen = []
    for p in parts:
        rows = p.read_text(encoding="utf-8").splitlines()
        assert rows[0] == "External ID,Name"          # header on every part
        assert len(rows) - 1 <= 1000                   # each part under the cap
        seen.extend(rows[1:])
    assert len(seen) == 2500                           # every row preserved


def test_split_csv_leaves_small_file_as_one_part(tmp_path):
    src = tmp_path / "small.csv"
    src.write_text("External ID,Name\nSANMAR-1,1\n", encoding="utf-8")
    parts = split_csv(src, tmp_path, "small", 1000)
    assert parts == [src]  # fits in one -> returned unchanged, no _partNN files

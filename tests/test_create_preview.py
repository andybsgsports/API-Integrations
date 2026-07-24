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


def test_split_csv_small_file_still_emits_part01(tmp_path):
    # The import step globs _part*.csv, so even a file that fits in one part
    # must be staged as part01 -- returning the un-split source made the
    # import silently no-op (live-run 30055141790).
    src = tmp_path / "small.csv"
    src.write_text("External ID,Name\nSANMAR-1,1\n", encoding="utf-8")
    parts = split_csv(src, tmp_path, "small", 1000)
    assert [p.name for p in parts] == ["small_part01.csv"]
    assert parts[0].read_text(encoding="utf-8") == "External ID,Name\nSANMAR-1,1\n"


def test_split_csv_no_rows_emits_no_parts(tmp_path):
    src = tmp_path / "empty.csv"
    src.write_text("External ID,Name\n", encoding="utf-8")
    assert split_csv(src, tmp_path, "empty", 1000) == []


def test_cap_children_skips_beyond_cap_and_recounts():
    from sanmar_create_preview import cap_children

    styles = [
        _style("K420", [_sku("A", "Black", "S"), _sku("B", "Navy", "S"),
                        _sku("E", "Navy", "M")]),
        _style("9999", [_sku("C", "Red", "S")]),  # net-new parent, never counts
    ]
    parent_refs = {"K420": "50286"}
    split = split_missing(styles, parent_refs, {"SANMAR-A"})
    assert split.new_children == 2  # B and E eligible

    cap_children(styles, parent_refs, split, 1)

    assert split.new_children == 1
    # exactly one of B/E stays importable; the other joins the skip set
    assert "SANMAR-B" not in split.child_only_skip
    assert "SANMAR-E" in split.child_only_skip


def test_cap_children_zero_is_noop():
    from sanmar_create_preview import cap_children

    styles = [_style("K420", [_sku("A", "Black", "S"), _sku("B", "Navy", "S")])]
    parent_refs = {"K420": "1"}
    split = split_missing(styles, parent_refs, set())
    before = set(split.child_only_skip)

    cap_children(styles, parent_refs, split, 0)

    assert split.new_children == 2
    assert split.child_only_skip == before

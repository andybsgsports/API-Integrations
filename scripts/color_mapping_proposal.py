"""Propose canonical color names for legacy item-name color tokens.

Works entirely from the two production exports in data/: parses every item
name as STYLE-COLOR-SIZE (style = Vendor Name column), collects color tokens
matching neither a color-list Name nor Abbreviation, and proposes a mapping:

* abbrev    -- token equals a list value's Abbreviation -> that value's Name
* heuristic -- token equals the initial-pairs abbreviation of exactly one
               Name (California Blue -> cabl)
* prefix    -- token is a unique prefix of exactly one Name
* compound  -- slash-separated parts each resolve (Bk/Wh -> Black/White)
               and the joined result exists in the list
* UNRESOLVED -- needs a human call

Output: data/color_token_mapping_proposal.csv for review/edit before any
write happens.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sanmar_netsuite.netsuite.adopt import heuristic_abbrev, split_color_size  # noqa: E402

# Common single-color abbreviations for compound (slash) tokens, derived from
# how the migrated names abbreviate; only used when the JOINED result is a
# real list value, so a wrong guess can't invent a color.
PART_ABBREVS = {
    "bk": "Black", "wh": "White", "na": "Navy", "gd": "Gold", "gr": "Grey",
    "ch": "Charcoal", "rd": "Red", "ro": "Royal", "sc": "Scarlet",
    "fo": "Forest", "gp": "Graphite", "si": "Silver", "vg": "Vegas Gold",
}


def load_colors() -> tuple[dict[str, str], dict[str, str]]:
    name_by_lower: dict[str, str] = {}
    abbrev_to_name: dict[str, str] = {}
    with (ROOT / "data" / "color_list_production.csv").open(
        encoding="utf-8-sig", newline=""
    ) as fh:
        for r in csv.DictReader(fh):
            nm, ab = r["Name"].strip(), (r.get("Abbreviation") or "").strip()
            if nm:
                name_by_lower[nm.lower()] = nm
            if ab and ab.lower() not in abbrev_to_name:
                abbrev_to_name[ab.lower()] = nm
    return name_by_lower, abbrev_to_name


def collect_tokens(name_by_lower: dict[str, str], abbrev_to_name: dict[str, str]):
    tokens: dict[str, int] = {}
    with (ROOT / "data" / "items_production.csv").open(
        encoding="utf-8-sig", newline=""
    ) as fh:
        for r in csv.DictReader(fh):
            itemid = (r.get("Name") or "").strip()
            style = (r.get("Vendor Name") or "").strip()
            if not style or itemid == style:
                continue
            cs = split_color_size(itemid, style)
            if not cs:
                continue
            color = cs[0]
            if color.lower() in name_by_lower:
                continue
            if color in ("X", "2X", "3X", "4X", "5X"):  # colorless-name artifact
                continue
            tokens[color] = tokens.get(color, 0) + 1
    return tokens


def propose(token: str, name_by_lower, abbrev_to_name) -> tuple[str, str, str]:
    """(proposed name, method, alternatives)"""
    low = token.lower()
    if low in abbrev_to_name:
        return abbrev_to_name[low], "abbrev", ""
    heur_hits = [
        nm for lower, nm in name_by_lower.items() if heuristic_abbrev(nm) == low
    ]
    if len(heur_hits) == 1:
        return heur_hits[0], "heuristic", ""
    prefix_hits = sorted(
        nm for lower, nm in name_by_lower.items() if lower.startswith(low)
    )
    if len(prefix_hits) == 1:
        return prefix_hits[0], "prefix", ""
    if "/" in token:
        parts = [PART_ABBREVS.get(p.strip().lower()) for p in token.split("/")]
        if all(parts):
            joined = "/".join(parts)  # type: ignore[arg-type]
            if joined.lower() in name_by_lower:
                return name_by_lower[joined.lower()], "compound", ""
    alts = "; ".join((heur_hits + prefix_hits)[:4])
    return "", "UNRESOLVED", alts


def main() -> int:
    name_by_lower, abbrev_to_name = load_colors()
    tokens = collect_tokens(name_by_lower, abbrev_to_name)
    out_path = ROOT / "data" / "color_token_mapping_proposal.csv"
    resolved = unresolved = 0
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["token", "item_count", "proposed_name", "method", "alternatives"])
        for token, n in sorted(tokens.items(), key=lambda kv: -kv[1]):
            proposed, method, alts = propose(token, name_by_lower, abbrev_to_name)
            w.writerow([token, n, proposed, method, alts])
            if proposed:
                resolved += 1
            else:
                unresolved += 1
    print(f"tokens: {len(tokens)} ({sum(tokens.values()):,} items)")
    print(f"auto-resolved: {resolved}; UNRESOLVED (needs a human call): {unresolved}")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Plan the misspelled-color consolidation, read-only (runs on CI).

The exact-name consolidation (``color_consolidation_plan.py``) merges values
that share a name. This one catches the *other* case Andy flagged: two values
that are the SAME colour spelled differently -- e.g. ``Forrest Green`` (672) vs
``Forest Green`` (1017). A misspelled value blocks adoption: the SanMar/S&S feed
sends ``Forest Green``, the item sits on ``Forrest Green``, nothing matches, so
the item never gets its UPC and the nightly update skips it (no data).

Detection: find colour-list values whose names are near-duplicates (Levenshtein
<= MAX_DISTANCE, default 2). Direction (which is the typo vs the keeper) is
decided by the *feed truth* -- the correctly-spelled colours already stored on
S&S items (``custitem_ss_color_name``). The side whose name matches a real feed
colour is canonical; the other is retired. Ties / no-feed-signal are emitted as
``review`` rows for a human to decide, never auto-applied.

Output (``data/color_misspell_plan.csv``) shares the retire_id/canonical_id
columns the repoint writer consumes, plus evidence columns (distance, item
counts, feed match, confidence) for review. Read-only: no writes here.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.adopt import COLOR_FIELD, COLOR_LIST
from sanmar_netsuite.netsuite.client import NetSuiteClient

ROOT = Path(__file__).resolve().parents[1]
MAX_DISTANCE = int(os.environ.get("COLOR_MISSPELL_MAX_DISTANCE", "2") or "2")


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def levenshtein(a: str, b: str, cap: int) -> int:
    """Edit distance a->b, short-circuiting once it exceeds ``cap``."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            cur.append(v)
            best = min(best, v)
        if best > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def load_feed_colors(client: NetSuiteClient) -> set[str]:
    """Correctly-spelled full colour names from the S&S feed, as stored on items
    (``custitem_ss_color_name``). This is the spelling truth for the direction
    call. SanMar stores an abbreviated mainframe colour, so it isn't a clean
    full-name source and is intentionally not used here."""
    out: set[str] = set()
    for r in client.suiteql(
        "SELECT DISTINCT custitem_ss_color_name AS c FROM item "
        "WHERE custitem_ss_color_name IS NOT NULL"
    ):
        n = _norm(str(r.get("c") or ""))
        if n:
            out.add(n)
    return out


def main() -> int:
    client = NetSuiteClient(get_config().netsuite)

    rows = client.suiteql(f"SELECT id, name FROM {COLOR_LIST}")
    name_by_id: dict[str, str] = {}
    values: list[tuple[str, str]] = []  # (id, normalized name)
    for r in rows:
        vid, nm = str(r["id"]), str(r.get("name") or "").strip()
        if not nm:
            continue
        name_by_id[vid] = nm
        values.append((vid, _norm(nm)))
    print(f"colour-list values: {len(values):,}")

    feed = load_feed_colors(client)
    print(f"S&S feed colour names (spelling truth): {len(feed):,}")

    usage: dict[str, int] = {}
    for r in client.suiteql(
        f"SELECT {COLOR_FIELD} AS c, COUNT(*) AS n FROM item "
        f"WHERE {COLOR_FIELD} IS NOT NULL GROUP BY {COLOR_FIELD}"
    ):
        usage[str(r["c"])] = int(r["n"])
    print(f"items with a colour option: {sum(usage.values()):,}")

    # Near-duplicate name pairs. O(n^2) over ~800 values is trivial; the
    # length-gap short-circuit in levenshtein keeps it cheap.
    seen_pairs: set[tuple[str, str]] = set()
    plan: list[dict[str, object]] = []
    for i in range(len(values)):
        id_a, na = values[i]
        for j in range(i + 1, len(values)):
            id_b, nb = values[j]
            if na == nb:
                continue  # exact-name dupes belong to the other planner
            d = levenshtein(na, nb, MAX_DISTANCE)
            if d > MAX_DISTANCE:
                continue
            key = tuple(sorted((id_a, id_b)))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)

            a_feed, b_feed = na in feed, nb in feed
            if a_feed and not b_feed:
                keep, retire = (id_a, na), (id_b, nb)
                conf = "auto"
            elif b_feed and not a_feed:
                keep, retire = (id_b, nb), (id_a, na)
                conf = "auto"
            else:
                # No clean feed signal (both or neither match): suggest the
                # higher-used value as keeper but leave it for human review.
                if usage.get(id_a, 0) >= usage.get(id_b, 0):
                    keep, retire = (id_a, na), (id_b, nb)
                else:
                    keep, retire = (id_b, nb), (id_a, na)
                conf = "review"
            # Distance 2 always gets a human look even with a feed signal.
            if d >= 2 and conf == "auto":
                conf = "review"
            plan.append({
                "retire_id": retire[0],
                "name": name_by_id[retire[0]],
                "canonical_id": keep[0],
                "canonical_name": name_by_id[keep[0]],
                "distance": d,
                "items_to_repoint": usage.get(retire[0], 0),
                "canonical_items": usage.get(keep[0], 0),
                "canonical_in_ss_feed": "yes" if keep[1] in feed else "no",
                "confidence": conf,
            })

    plan.sort(key=lambda r: (r["confidence"], -int(r["items_to_repoint"]),
                             str(r["name"]).lower()))
    out = ROOT / "data" / "color_misspell_plan.csv"
    cols = ["retire_id", "name", "canonical_id", "canonical_name", "distance",
            "items_to_repoint", "canonical_items", "canonical_in_ss_feed",
            "confidence"]
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(plan)

    autos = [p for p in plan if p["confidence"] == "auto"]
    auto_items = sum(int(p["items_to_repoint"]) for p in autos)
    review_items = sum(int(p["items_to_repoint"]) for p in plan if p["confidence"] == "review")
    print(f"\nnear-duplicate colour pairs: {len(plan):,}")
    print(f"  auto (one side matches the feed, distance 1): {len(autos):,} "
          f"-> {auto_items:,} items to repoint")
    print(f"  review (ambiguous direction / distance 2): {len(plan) - len(autos):,} "
          f"-> {review_items:,} items")
    # Spotlight the case Andy raised so it's obvious in the log.
    for p in plan:
        if "forrest" in (str(p["name"]) + str(p["canonical_name"])).lower():
            print(f"  e.g. retire {p['retire_id']} {p['name']!r} -> "
                  f"{p['canonical_id']} {p['canonical_name']!r} "
                  f"({p['items_to_repoint']} items, {p['confidence']})")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

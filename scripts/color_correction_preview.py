"""Read-only preview of the per-STYLE colour correction.

Some migrated items were mislabelled at the item level -- e.g. style 695HBM's
Collegiate Blue variant was stored as "Columbia Blue" (its True Red as "Red",
its J.Navy as "Navy"). This is NOT a general colour equivalence: Collegiate Blue
is a different colour from Columbia Blue elsewhere, so the fix is scoped to the
STYLE and never a global colour rename. Because the item sits on the wrong
colour, the vendor feed (which sends the correct name) can't match it, so it
gets no data.

``data/color_corrections.csv`` lists ``(style, current_color, correct_color)``.
This preview shows, per row, how many of that style's items sit on the wrong
colour and whether the correct colour value already exists (or must be created
first). No writes -- the apply step repoints those items after review.
"""

from __future__ import annotations

import csv
from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.adopt import COLOR_FIELD, OptionMaps
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape

ROOT = Path(__file__).resolve().parents[1]


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def load_corrections() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    with (ROOT / "data" / "color_corrections.csv").open(
        encoding="utf-8-sig", newline=""
    ) as fh:
        for r in csv.DictReader(fh):
            st = (r.get("style") or "").strip()
            cur = (r.get("current_color") or "").strip()
            cor = (r.get("correct_color") or "").strip()
            if st and cur and cor:
                rows.append((st, cur, cor))
    return rows


def main() -> int:
    client = NetSuiteClient(get_config().netsuite)
    corrections = load_corrections()
    print(f"corrections loaded: {len(corrections)}")
    options = OptionMaps(client)

    style_items: dict[str, list[dict]] = {}
    out_rows: list[dict] = []
    summary: list[tuple] = []
    for style, cur, cor in corrections:
        if style not in style_items:
            safe = _sql_escape(style)
            style_items[style] = client.suiteql(
                f"SELECT id, itemid, {COLOR_FIELD} AS color "
                f"FROM item WHERE vendorname = '{safe}'"
            )
        cur_ids = set(options.color_by_name.get(_norm(cur), []))
        cor_ids = options.color_by_name.get(_norm(cor), [])
        hits = [r for r in style_items[style]
                if str(r.get("color") or "") in cur_ids]
        exists = f"yes (id {cor_ids[0]})" if cor_ids else "NO -- needs creating"
        summary.append((style, cur, cor, len(hits), exists))
        for r in hits:
            out_rows.append({
                "style": style, "item_id": str(r["id"]),
                "itemid": str(r.get("itemid") or ""),
                "current_color": cur, "correct_color": cor,
                "correct_value_exists": "yes" if cor_ids else "no",
            })

    out = ROOT / "data" / "color_correction_preview.csv"
    cols = ["style", "item_id", "itemid", "current_color", "correct_color",
            "correct_value_exists"]
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(sorted(out_rows, key=lambda r: (r["style"], r["current_color"],
                                                    r["itemid"])))

    print("\nper-style colour corrections (scoped -- never a global rename):")
    for style, cur, cor, n, exists in summary:
        print(f"  {style}: {cur!r} -> {cor!r}: {n} item(s); target value {exists}")
    need_create = sorted({(s, cor) for s, _cur, cor, _n, ex in summary
                          if "NO" in ex})
    if need_create:
        print("\ntarget colour values that must be CREATED before applying:")
        for _s, cor in need_create:
            print(f"  {cor!r}")
    print(f"\ntotal items to correct: {len(out_rows)}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

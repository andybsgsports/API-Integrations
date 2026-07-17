"""Read-only color audit: how consistent are item colors with the BSG
matrix color list? (Runs on CI.)

Sections:
1. The color list itself -- size, and values that look like migration-era
   abbreviations (no space, short, e.g. "cabl") rather than real names.
2. Matrix children with / without a color option assigned.
3. Items whose assigned color-list value NAME disagrees with the supplier's
   color name for the same item (S&S carries the full color name; SanMar
   carries the abbreviated mainframe color, compared to the list value's
   Abbreviation instead).
4. Distinct supplier color names that match NO list value at all -- these
   are what blocks adoption/matching for those SKUs.
5. Sandbox list vs the PRODUCTION export the user provided
   (data/color_list_production.csv) -- drift by internal id.
"""

from __future__ import annotations

import csv
from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.adopt import COLOR_FIELD, COLOR_LIST
from sanmar_netsuite.netsuite.client import NetSuiteClient

SS_COLOR = "custitem_ss_color_name"
SANMAR_MF = "custitem_sanmar_mf_color"
PROD_CSV = Path(__file__).resolve().parents[1] / "data" / "color_list_production.csv"


def main() -> int:
    client = NetSuiteClient(get_config().netsuite)

    print("=== 1. color list ===")
    try:
        rows = client.suiteql(f"SELECT id, name, abbreviation FROM {COLOR_LIST}")
    except Exception:  # noqa: BLE001
        rows = client.suiteql(f"SELECT id, name FROM {COLOR_LIST}")
    names = {str(r["id"]): str(r.get("name") or "").strip() for r in rows}
    abbrevs = {str(r["id"]): str(r.get("abbreviation") or "").strip() for r in rows}
    print(f"color list values: {len(rows):,}")
    suspicious = sorted(
        n for n in names.values()
        if n and " " not in n and len(n) <= 5 and n.lower() == n
    )
    print(f"abbreviation-looking names (short, lowercase, no space): {len(suspicious)}")
    for n in suspicious[:30]:
        print(f"  {n!r}")

    print("\n=== 2. matrix children color coverage ===")
    n_children = int(client.suiteql(
        "SELECT COUNT(*) AS n FROM item WHERE parent IS NOT NULL"
    )[0]["n"])
    n_with = int(client.suiteql(
        f"SELECT COUNT(*) AS n FROM item WHERE parent IS NOT NULL AND {COLOR_FIELD} IS NOT NULL"
    )[0]["n"])
    print(f"children: {n_children:,}; with color option: {n_with:,}; "
          f"WITHOUT: {n_children - n_with:,}")

    print("\n=== 3. assigned color vs supplier color name ===")
    try:
        total = int(client.suiteql(
            f"SELECT COUNT(*) AS n FROM item i JOIN {COLOR_LIST} c ON c.id = i.{COLOR_FIELD} "
            f"WHERE i.{SS_COLOR} IS NOT NULL AND LOWER(c.name) <> LOWER(i.{SS_COLOR})"
        )[0]["n"])
        print(f"items where list value name <> S&S color name: {total:,}")
        for r in client.suiteql(
            f"SELECT c.name AS list_name, i.{SS_COLOR} AS feed_name, COUNT(*) AS n "
            f"FROM item i JOIN {COLOR_LIST} c ON c.id = i.{COLOR_FIELD} "
            f"WHERE i.{SS_COLOR} IS NOT NULL AND LOWER(c.name) <> LOWER(i.{SS_COLOR}) "
            f"GROUP BY c.name, i.{SS_COLOR} ORDER BY COUNT(*) DESC FETCH FIRST 30 ROWS ONLY"
        ):
            print(f"  list={r.get('list_name')!r:30} feed={r.get('feed_name')!r:30} x{int(r['n'])}")
    except Exception as exc:  # noqa: BLE001
        print(f"  (S&S comparison failed: {str(exc)[:150]})")

    print("\n=== 4. supplier colors matching NO list value ===")
    known = {n.lower() for n in names.values() if n} | {
        a.lower() for a in abbrevs.values() if a
    }
    for field, label in ((SS_COLOR, "S&S color names"), (SANMAR_MF, "SanMar mainframe colors")):
        try:
            rows = client.suiteql(
                f"SELECT DISTINCT {field} AS v FROM item WHERE {field} IS NOT NULL"
            )
            vals = {str(r["v"]).strip() for r in rows if str(r.get("v") or "").strip()}
            missing = sorted(v for v in vals if v.lower() not in known)
            print(f"{label}: {len(vals):,} distinct; unmatched by list name/abbrev: {len(missing)}")
            for v in missing[:25]:
                print(f"  {v!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ({label} scan failed: {str(exc)[:120]})")

    print("\n=== 5. sandbox list vs production export ===")
    if not PROD_CSV.exists():
        print(f"  ({PROD_CSV.name} not found)")
        return 0
    prod: dict[str, tuple[str, str]] = {}
    with PROD_CSV.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            prod[str(r["Internal ID"]).strip()] = (
                (r.get("Name") or "").strip(),
                (r.get("Abbreviation") or "").strip(),
            )
    print(f"production values: {len(prod):,}; sandbox values: {len(names):,}")
    only_prod = sorted(set(prod) - set(names), key=int)
    only_sb = sorted(set(names) - set(prod), key=int)
    print(f"ids only in production (new since sandbox copy): {len(only_prod)}")
    for vid in only_prod[:20]:
        print(f"  {vid}: {prod[vid][0]!r}")
    print(f"ids only in sandbox: {len(only_sb)}")
    for vid in only_sb[:20]:
        print(f"  {vid}: {names[vid]!r}")
    renamed = [
        (vid, names[vid], prod[vid][0])
        for vid in set(prod) & set(names)
        if names[vid].strip().casefold() != prod[vid][0].strip().casefold()
    ]
    print(f"same id, DIFFERENT name (drift): {len(renamed)}")
    for vid, sb, pr in sorted(renamed, key=lambda t: int(t[0]))[:30]:
        print(f"  {vid}: sandbox={sb!r} production={pr!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

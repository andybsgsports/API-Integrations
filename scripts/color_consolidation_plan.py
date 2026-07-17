"""Plan the duplicate-color consolidation, read-only (runs on CI).

The color list carries duplicate values -- same name, different internal ids
(production has 33 duplicated names; the sandbox list ballooned to ~4,800
values during the catalog imports). Consolidation = every item pointing at a
duplicate gets repointed to ONE canonical value per name.

Canonical choice per (case-insensitive) name:
1. the id that exists in the PRODUCTION export (authoritative), or
2. the lowest sandbox id (oldest value) when production doesn't have it.

Output (committed as data/color_consolidation_plan.csv): one row per
duplicate value to retire -- its id/name, the canonical id it merges into,
and how many items currently point at it. The repoint writer consumes this
after review.
"""

from __future__ import annotations

import csv
from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.adopt import COLOR_FIELD, COLOR_LIST
from sanmar_netsuite.netsuite.client import NetSuiteClient

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    client = NetSuiteClient(get_config().netsuite)

    prod_id_by_name: dict[str, str] = {}
    with (ROOT / "data" / "color_list_production.csv").open(
        encoding="utf-8-sig", newline=""
    ) as fh:
        for r in csv.DictReader(fh):
            nm = (r.get("Name") or "").strip().lower()
            # first occurrence wins -- production's own duplicates collapse
            # to their lowest-id row (the export is id-ordered per name).
            if nm and nm not in prod_id_by_name:
                prod_id_by_name[nm] = str(r["Internal ID"]).strip()

    rows = client.suiteql(f"SELECT id, name FROM {COLOR_LIST}")
    by_name: dict[str, list[str]] = {}
    names_by_id: dict[str, str] = {}
    for r in rows:
        vid, nm = str(r["id"]), str(r.get("name") or "").strip()
        names_by_id[vid] = nm
        if nm:
            by_name.setdefault(nm.lower(), []).append(vid)
    print(f"sandbox color values: {len(rows):,}; distinct names: {len(by_name):,}")
    dupes = {nm: sorted(ids, key=int) for nm, ids in by_name.items() if len(ids) > 1}
    print(f"names with duplicates in sandbox: {len(dupes):,}")

    usage: dict[str, int] = {}
    for r in client.suiteql(
        f"SELECT {COLOR_FIELD} AS c, COUNT(*) AS n FROM item "
        f"WHERE {COLOR_FIELD} IS NOT NULL GROUP BY {COLOR_FIELD}"
    ):
        usage[str(r["c"])] = int(r["n"])

    out = ROOT / "data" / "color_consolidation_plan.csv"
    total_items = 0
    kept = retired = 0
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["retire_id", "name", "canonical_id", "items_to_repoint", "canonical_source"])
        for nm, ids in sorted(dupes.items()):
            prod_id = prod_id_by_name.get(nm)
            if prod_id and prod_id in ids:
                canonical, source = prod_id, "production"
            else:
                canonical, source = ids[0], "lowest-id"
            kept += 1
            for vid in ids:
                if vid == canonical:
                    continue
                n = usage.get(vid, 0)
                total_items += n
                retired += 1
                w.writerow([vid, names_by_id[vid], canonical, n, source])
    print(f"duplicate values to retire: {retired:,} (keeping {kept:,} canonicals)")
    print(f"items needing repointing: {total_items:,}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

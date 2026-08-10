"""Ensure the matrix Color / Size list values the create-import needs actually
exist -- creating the genuinely-new ones, and flagging spelling variants of
values you already have (so we remap those instead of duplicating them).

The CSV create-import writes the SanMar feed's raw colour/size *names* into the
matrix option fields, matched by name against the custom lists. Any name the list
doesn't carry fails the child with ``Invalid matrixoptioncustitem_bsg_color
reference key <name>``. The failed rows give us the missing names
(``data/sanmar_missing_colors.txt`` / ``data/sanmar_missing_sizes.txt``).

Each missing name is classified against the live list:

* **variant** -- a value already exists whose name matches once punctuation and
  spacing are ignored (e.g. feed ``J. Navy`` vs list ``J.Navy``,
  ``Anthracite Heather/ Black`` vs ``Anthracite Heather/Black``). These are NOT
  created -- creating them would duplicate the colour (the Forest/Forrest mess).
  They're written to a remap file so the retry CSV can use the canonical name.
* **new** -- no match at all; a real SanMar colour BSG doesn't carry yet. Created
  in the list (unique abbreviation) so the retry import can attach it.

Dry-run by default (``SYNC_DRY_RUN``): reports the new/variant split and writes
``data/sanmar_color_plan.csv`` + ``data/sanmar_size_plan.csv`` without creating
anything. ``CREATE_MAX`` caps live creates.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.adopt import COLOR_LIST, SIZE_LIST, heuristic_abbrev
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.matrix_options import normalize_option_name as _norm
from sanmar_netsuite.transform.sizes import normalize_size

ROOT = Path(__file__).resolve().parents[1]

# _norm is imported, not redefined: this pre-pass and the create path's
# MatrixOptionResolver must share ONE definition of "same option value".
# When they didn't, this script correctly refused to create 'J. Navy' as a
# duplicate of 'J.Navy' and the resolver then created it anyway.


def classify(
    missing: list[str], existing_by_norm: dict[str, str]
) -> tuple[list[str], dict[str, str]]:
    """Split missing names into (new, {variant_name: canonical_existing_name})."""
    new: list[str] = []
    variants: dict[str, str] = {}
    for name in missing:
        canonical = existing_by_norm.get(_norm(name))
        if canonical is not None and canonical != name:
            variants[name] = canonical
        elif canonical is None:
            new.append(name)
        # canonical == name would mean it already exists exactly (not missing)
    return new, variants


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _input_file(env_var: str, preferred: str, fallback: str) -> Path:
    """Resolve which name list to read.

    Order: an explicit env override, else the create-preview's "new children"
    list (the pre-flight input -- every colour/size the pending import uses),
    else the failure-derived "missing" list (retro-fixing an import that already
    ran). Lets ensure-values run as a pre-import gate or a post-mortem cleanup.
    """
    override = os.environ.get(env_var)
    if override:
        return ROOT / override
    pref = ROOT / "data" / preferred
    return pref if pref.exists() else ROOT / "data" / fallback


def _unique_abbrev(base: str, used: set[str]) -> str:
    ab = (base or "c")[:10]
    cand, i = ab, 1
    while cand.lower() in used:
        suffix = str(i)
        cand = ab[: 10 - len(suffix)] + suffix
        i += 1
    used.add(cand.lower())
    return cand


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    create_max = int(os.environ.get("CREATE_MAX", "0") or "0")
    client = NetSuiteClient(cfg.netsuite)

    # Live lists (original-case names + used abbreviations).
    color_rows = client.suiteql(f"SELECT id, name, abbreviation FROM {COLOR_LIST}")
    size_rows = client.suiteql(f"SELECT id, name FROM {SIZE_LIST}")
    color_by_norm = {_norm(str(r["name"])): str(r["name"]) for r in color_rows if r.get("name")}
    size_by_norm = {_norm(str(r["name"])): str(r["name"]) for r in size_rows if r.get("name")}
    used_abbrev = {str(r.get("abbreviation") or "").lower() for r in color_rows}
    used_abbrev.discard("")

    colors_file = _input_file(
        "ENSURE_COLORS_FILE", "sanmar_new_children_colors.txt", "sanmar_missing_colors.txt"
    )
    sizes_file = _input_file(
        "ENSURE_SIZES_FILE", "sanmar_new_children_sizes.txt", "sanmar_missing_sizes.txt"
    )
    print(f"reading colours from {colors_file.name}, sizes from {sizes_file.name}")
    missing_colors = _read_lines(colors_file)
    missing_sizes = [normalize_size(s) for s in _read_lines(sizes_file)]

    new_colors, color_variants = classify(missing_colors, color_by_norm)
    new_sizes, size_variants = classify(missing_sizes, size_by_norm)

    print(f"colors: {len(missing_colors)} missing -> {len(new_colors)} NEW, "
          f"{len(color_variants)} variant(s) of existing values")
    print(f"sizes : {len(missing_sizes)} missing -> {len(new_sizes)} NEW, "
          f"{len(size_variants)} variant(s) of existing values")

    data = ROOT / "data"
    data.mkdir(parents=True, exist_ok=True)
    with (data / "sanmar_color_plan.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["missing_color", "disposition", "maps_to"])
        for c in sorted(new_colors):
            w.writerow([c, "CREATE", ""])
        for c, canon in sorted(color_variants.items()):
            w.writerow([c, "VARIANT", canon])
    with (data / "sanmar_size_plan.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["missing_size", "disposition", "maps_to"])
        for s in sorted(new_sizes):
            w.writerow([s, "CREATE", ""])
        for s, canon in sorted(size_variants.items()):
            w.writerow([s, "VARIANT", canon])

    if color_variants:
        print("\nsample colour variants (map to existing, NOT created):")
        for c, canon in list(sorted(color_variants.items()))[:12]:
            print(f"  {c!r} -> {canon!r}")

    created = failures = 0
    for name in sorted(new_colors):
        if create_max and created >= create_max:
            break
        if not allow_write:
            created += 1
            if created <= 12:
                print(f"  WOULD create colour {name!r}")
            continue
        try:
            abbrev = _unique_abbrev(heuristic_abbrev(name), used_abbrev)
            client.create_record(COLOR_LIST, {"name": name, "abbreviation": abbrev})
            created += 1
        except Exception as exc:  # noqa: BLE001
            failures += 1
            if failures <= 10:
                print(f"  FAILED colour {name!r}: {str(exc)[:120]} "
                      f":: {str(getattr(exc, 'payload', ''))[:200]}")

    size_created = 0
    for name in sorted(new_sizes):
        if not allow_write:
            size_created += 1
            continue
        try:
            client.create_record(SIZE_LIST, {"name": name})
            size_created += 1
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  FAILED size {name!r}: {str(exc)[:120]}")

    verb = "created" if allow_write else "WOULD create (dry run)"
    print(f"\nensure values: {verb} {created} colour(s) + {size_created} size(s); "
          f"variants left for remap: {len(color_variants) + len(size_variants)}; "
          f"failures: {failures}")
    print("plans written: data/sanmar_color_plan.csv, data/sanmar_size_plan.csv")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

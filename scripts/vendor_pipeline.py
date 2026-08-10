"""One vendor, start to finish, before the next vendor starts.

The nightly used to run seven independent workflows on guessed clock times,
each doing one narrow job, with **item creation left out entirely** (manual
dispatch, pilot-capped at 5 styles). The result: the enrichment jobs polished
the ~26% of the catalogue that happened to exist while three quarters of
SanMar's line -- 3,495 styles / ~115k SKUs as of the 2026-07-24 preview --
had never been created at all.

Per Andy (2026-07-31), each vendor now runs as ONE ordered pipeline, and the
next vendor does not start until this one has finished all four phases:

  1. DISCOVER -- diff the vendor's feed against the live catalogue: which
     styles/SKUs are missing, and which colour/size option values the new
     items will need.
  2. OPTIONS  -- create the genuinely-new colour/size list values FIRST, since
     an item cannot reference an option that doesn't exist. Punctuation
     variants of values already on the list ('Black/ Red' vs 'Black/Red') are
     remapped to the existing value, never duplicated -- that is what produced
     the Forest/Forrest mess.
  3. CREATE   -- matrix parents, then their children, with the SAME native
     pricing rules the update phase uses so an item is never born with numbers
     that a later job has to correct.
  4. UPDATE   -- the vendor's full field pass over ALL of its items, new and
     existing: inventory, purchase price, on-sale, closeout, every
     ``custitem_*`` field, and the lifecycle heartbeat.

Phase 4 is deliberately the same script that always ran: it is the single
source of truth for "every field", so creation stays thin (identity + correct
pricing) instead of duplicating ~30 field mappings that would drift.

Ramp: ``CREATE_MAX_STYLES`` caps how many net-new styles are created per
vendor per night (default 300 -- Andy's "ramped in over nights" choice), so
the first batches can be eyeballed before the whole catalogue lands. Raise it
to 0 for no cap once the shape looks right.

Usage::

    python scripts/vendor_pipeline.py --vendor sanmar
    python scripts/vendor_pipeline.py --vendor sanmar --phases update
    python scripts/vendor_pipeline.py --list
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Vendor run order (Andy, 2026-07-31). Order no longer decides any VALUE --
#: pricing ownership follows the Preferred Vendor flag (pricing_ownership.py),
#: not whoever wrote last -- so this is purely about sequencing the work.
VENDOR_ORDER = ["sanmar", "momentec", "ua", "ss", "dcos", "champro-csv"]

PHASES = ("discover", "options", "create", "update")

#: Per-vendor phase -> script(s). A phase absent from a vendor's map has no
#: implementation for that vendor yet; the pipeline says so out loud and moves
#: on rather than pretending the phase ran.
PIPELINES: dict[str, dict[str, list[str]]] = {
    "sanmar": {
        # Reads the SDL feed + live catalogue; writes the net-new style list
        # and the colour/size lists the new children reference.
        "discover": ["scripts/sanmar_create_preview.py"],
        # Creates genuinely-new colours/sizes; remaps punctuation variants.
        "options": ["scripts/sanmar_ensure_matrix_values.py"],
        # New children under EXISTING parents go via the CSV import map;
        # net-new styles get parent-then-children via REST + the matrix
        # RESTlet; child_finalize then copies Department/Class down from each
        # parent (children deliberately aren't sent a class -- the RESTlet's
        # class-path search crashes on this account, the childless-parent bug
        # from the live pilot).
        "create": [
            "scripts/sanmar_csv_import.py",
            "scripts/sanmar_parent_create.py",
            "scripts/sanmar_child_finalize.py",
        ],
        # atlas_image_backfill runs AFTER the field update because it joins
        # items by custitem_sanmar_unique_key, which the field update stamps
        # (matched by UPC) -- a child created minutes earlier has no key yet.
        # It uploads each colour's image to the File Cabinet once and links
        # the real Image field; diff-aware, so it only touches imageless
        # items (Andy, 2026-08-05: images are part of creation).
        "update": [
            "scripts/sanmar_field_update.py",
            "scripts/atlas_image_backfill.py",
        ],
    },
    "momentec": {
        # No creation path built yet -- see PIPELINE_GAPS below.
        "update": ["scripts/momentec_backfill.py"],
    },
    "ua": {
        "update": ["scripts/ua_backfill.py"],
    },
    "ss": {
        "update": ["scripts/ss_backfill.py"],
    },
    "dcos": {
        # dcos_item_create resolves/creates its own option values inline, so
        # it covers the options phase for these suppliers itself.
        "create": ["scripts/dcos_item_create.py"],
        "update": ["scripts/dcos_backfill.py"],
    },
    "champro-csv": {
        "update": ["scripts/champro_csv_backfill.py"],
    },
}

#: Vendors whose CREATE path doesn't exist yet, and why it isn't a one-liner.
#: Printed by --list so the gap is visible instead of implied by an empty map.
PIPELINE_GAPS = {
    "momentec": "matches on matrix options only (no barcode), so creation needs "
                "a style->parent mapping built first",
    "ua": "DC OneSource parts carry no style grid; parent structure per style "
          "needs deciding before children can be created",
    "ss": "195k-SKU feed; needs the same preview/diff stage SanMar has before "
          "anything is created",
}

#: Vendor -> env the phase scripts expect (supplier selector, mostly).
VENDOR_ENV: dict[str, dict[str, str]] = {
    "dcos": {"DCOS_SUPPLIER": os.environ.get("DCOS_SUPPLIER", "champro")},
}

#: Default per-vendor-per-night cap on NET-NEW styles created (0 = no cap).
DEFAULT_CREATE_MAX_STYLES = "300"


def _run(script: str, env: dict[str, str]) -> int:
    """Run one phase script, streaming its output. Returns its exit code."""
    path = ROOT / script
    if not path.exists():
        print(f"  !! {script} not found -- skipping", flush=True)
        return 0
    t0 = time.monotonic()
    print(f"\n--- {script}", flush=True)
    proc = subprocess.run([sys.executable, str(path)], env=env, cwd=ROOT, check=False)
    print(f"--- {script} finished rc={proc.returncode} "
          f"in {time.monotonic() - t0:,.1f}s", flush=True)
    return proc.returncode


def run_vendor(vendor: str, phases: tuple[str, ...] = PHASES) -> int:
    """Run a vendor's pipeline in phase order. Returns the worst exit code.

    A failing phase does NOT abort the vendor: every phase is diff-aware and
    idempotent, so finishing the remaining phases still moves the catalogue
    forward and the next run picks up whatever was missed. The overall exit
    code stays non-zero so CI still opens the failure issue.
    """
    pipeline = PIPELINES.get(vendor)
    if pipeline is None:
        print(f"unknown vendor {vendor!r}; known: {', '.join(PIPELINES)}")
        return 1

    env = dict(os.environ)
    env.setdefault("CREATE_MAX_STYLES", DEFAULT_CREATE_MAX_STYLES)
    env.update(VENDOR_ENV.get(vendor, {}))

    print(f"\n{'=' * 62}\n=== VENDOR: {vendor}  "
          f"(phases: {', '.join(phases)})\n{'=' * 62}", flush=True)
    worst = 0
    for phase in phases:
        scripts = pipeline.get(phase)
        if not scripts:
            gap = PIPELINE_GAPS.get(vendor, "")
            note = f" -- {gap}" if (gap and phase == "create") else ""
            print(f"\n[{vendor}: {phase}] no implementation yet, skipping{note}",
                  flush=True)
            continue
        print(f"\n[{vendor}: {phase}]", flush=True)
        for script in scripts:
            worst = max(worst, _run(script, env))
    print(f"\n=== VENDOR {vendor} complete (worst rc={worst})", flush=True)
    return worst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vendor", help="vendor key, or 'all' for every vendor in order")
    ap.add_argument("--phases", default=",".join(PHASES),
                    help=f"comma-separated subset of: {','.join(PHASES)}")
    ap.add_argument("--list", action="store_true", help="show the configured pipelines")
    args = ap.parse_args()

    if args.list or not args.vendor:
        print(f"vendor order: {' -> '.join(VENDOR_ORDER)}\n")
        for v in VENDOR_ORDER:
            have = [p for p in PHASES if PIPELINES.get(v, {}).get(p)]
            missing = [p for p in PHASES if p not in have]
            print(f"  {v:12s} phases: {', '.join(have) or '(none)'}")
            if missing:
                gap = PIPELINE_GAPS.get(v)
                print(f"  {'':12s}   missing: {', '.join(missing)}"
                      + (f"  [{gap}]" if gap else ""))
        return 0

    phases = tuple(p.strip() for p in args.phases.split(",") if p.strip())
    bad = [p for p in phases if p not in PHASES]
    if bad:
        print(f"unknown phase(s): {bad}; valid: {', '.join(PHASES)}")
        return 1

    vendors = VENDOR_ORDER if args.vendor == "all" else [args.vendor]
    worst = 0
    for vendor in vendors:
        worst = max(worst, run_vendor(vendor, phases))
    return worst


if __name__ == "__main__":
    raise SystemExit(main())

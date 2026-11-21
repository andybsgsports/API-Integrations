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
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import price_guard

ROOT = Path(__file__).resolve().parents[1]

#: Vendor run order (Andy, 2026-07-31). Order no longer decides any VALUE --
#: pricing ownership follows the Preferred Vendor flag (pricing_ownership.py),
#: not whoever wrote last -- so this is purely about sequencing the work.
VENDOR_ORDER = ["sanmar", "momentec", "ua", "ss", "dcos", "champro-csv"]

#: ``images`` trails ``update`` because it depends on the key that phase
#: stamps; it is a separate phase so it can be its own chain step with its own
#: job budget (see the SanMar note in PIPELINES).
PHASES = ("discover", "options", "create", "update", "images")

#: Phases that run only when asked for by name -- never part of the default
#: run or the nightly chain. ``link`` is the read-only report that matches
#: existing items to feed SKUs before anything is linked (Andy, 2026-11-20).
ON_REQUEST_PHASES = ("link", "link-apply")

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
        # New children under EXISTING parents post through the matrix RESTlet
        # (sanmar_child_create) -- the CSV-import Suitelet it replaced came
        # back SSS_INVALID_SCRIPTLET_ID on 2026-08-11 (issue #116), and the
        # RESTlet path is idempotent and carries the full field spec the CSV
        # map never did. Net-new styles get parent-then-children via REST +
        # the same RESTlet; child_finalize then copies Department/Class down
        # from each parent (children deliberately aren't sent a class -- the
        # RESTlet's class-path search crashes on this account, the
        # childless-parent bug from the live pilot).
        "create": [
            "scripts/sanmar_child_create.py",
            "scripts/sanmar_parent_create.py",
            "scripts/sanmar_child_finalize.py",
        ],
        "update": ["scripts/sanmar_field_update.py",
                   "scripts/vendor_line_price_sync.py"],
        # atlas_image_backfill runs AFTER the field update because it joins
        # items by custitem_sanmar_unique_key, which the field update stamps
        # (matched by UPC) -- a child created minutes earlier has no key yet.
        # It uploads each colour's image to the File Cabinet once and links
        # the real Image field; diff-aware, so it only touches imageless
        # items (Andy, 2026-08-05: images are part of creation).
        #
        # Its own chain STEP, not part of update: the two together ran 295+
        # minutes against the 350-minute job cap on 2026-08-10 (run
        # 31424046955, the night swatch URLs first went out to ~62k items),
        # and a leg killed by the cap never reaches the dispatch step -- so
        # every vendor after SanMar would silently lose the night.
        "images": ["scripts/atlas_image_backfill.py"],
        # Read-only: which existing items match a feed size and colour, and
        # why the rest do not. On request only (ON_REQUEST_PHASES).
        "link": ["scripts/sanmar_link_report.py"],
        # Stamps the UPC and Sanmar unique key on clean matches only, capped
        # by MAX_ITEMS. On request only; writes only in live mode.
        "link-apply": ["scripts/sanmar_link_apply.py"],
    },
    "momentec": {
        # Parents then children for styles NetSuite does not have, plus
        # missing colours/sizes under existing parents; resolves its own
        # option values. NOT in the nightly chain's momentec step yet (the
        # chain runs "update" only): it wants a dry run and a small pilot
        # Andy reviews first, the way SanMar's creation rolled out.
        "create": ["scripts/momentec_create.py"],
        "update": ["scripts/momentec_backfill.py",
                   "scripts/vendor_line_price_sync.py"],
    },
    "ua": {
        "update": ["scripts/ua_backfill.py",
                   "scripts/vendor_line_price_sync.py"],
    },
    "ss": {
        # Read-only diff of the 195k-SKU catalog: buckets missing SKUs into
        # adopt-a-parent children vs net-new parent styles vs itemid
        # collisions, and emits the create/ensure-values inputs.
        "discover": ["scripts/ss_create_preview.py"],
        # Parents where none exists, then children -- adopting whatever parent
        # already carries the style code (grid probe run 31437315008 proved a
        # parent does not restrict its children's option values). NOT wired
        # into the nightly chain's ss leg yet: it wants a dry run and a small
        # pilot Andy reviews first, the way SanMar's creation rolled out.
        "create": ["scripts/ss_create.py"],
        "update": ["scripts/ss_backfill.py",
                   "scripts/vendor_line_price_sync.py"],
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
    "momentec": "create is built (momentec_create.py) but deliberately NOT "
                "in the chain's momentec step: it wants a dry run and a small "
                "pilot Andy reviews first.",
    "ua": "DC OneSource parts carry no style grid; parent structure per style "
          "needs deciding before children can be created",
    "ss": "create is built (ss_create.py) but deliberately NOT in the "
          "chain's ss leg: it wants a dry run and a small pilot Andy "
          "reviews first, the way SanMar's creation rolled out. Scope is "
          "the brand allowlist minus the Momentec-direct brands.",
}

#: S&S creation is scoped to brands BSG actually sells, not the whole 5,455
#: style catalogue (Andy, 2026-08-10: "specific brands", then "all 36 brands
#: you carry today"). CARRIED resolves that live from the catalogue each run
#: -- 36 frozen names would drift the moment the catalogue moves, and the
#: discover run prints the resolved set so scope changes are visible.
#: Override per run with SS_CREATE_BRANDS.
SS_DEFAULT_BRANDS = "CARRIED"

#: The DC OneSource suppliers the nightly covers -- every heartbeat source
#: lifecycle expects from this leg (tck/capamerica/mizuno) plus champro's
#: create-enabled catalogue. Override with a comma list in DCOS_SUPPLIERS.
DCOS_SUPPLIERS = tuple(
    s.strip().lower()
    for s in os.environ.get("DCOS_SUPPLIERS", "champro,tck,capamerica,mizuno").split(",")
    if s.strip()
)

#: Vendor -> env the phase scripts expect (supplier selector, mostly).
VENDOR_ENV: dict[str, dict[str, str]] = {
    # VENDOR_LINE_SOURCE tells vendor_line_price_sync.py whose Preferred
    # Vendor lines this leg keeps equal to cost.
    "sanmar": {"VENDOR_LINE_SOURCE": "sanmar"},
    "momentec": {"VENDOR_LINE_SOURCE": "momentec"},
    "ua": {"VENDOR_LINE_SOURCE": "ua"},
    "ss": {"SS_CREATE_BRANDS": os.environ.get("SS_CREATE_BRANDS")
           or SS_DEFAULT_BRANDS,
           "VENDOR_LINE_SOURCE": "ss"},
}

#: Default per-vendor-per-night cap on NET-NEW styles created (0 = no cap).
DEFAULT_CREATE_MAX_STYLES = "300"


#: Output lines worth repeating in the run summary: each script's totals line,
#: everything skipped or failed by name, and warnings / notes / price holds.
_SUMMARY_LINE = re.compile(
    r"failures: \d|SKIPPED|FAILED item|^WARNING|^NOTE|PRICE HOLD|PRICE GUARD"
    r"|WOULD |dropped on |^LINK REPORT|^LINK APPLY"
)
#: Cap on named skip / failure lines kept per script so one bad night cannot
#: produce a summary nobody can read. The totals line always carries the count.
SUMMARY_MAX_DETAIL = 200

#: Collected for the run summary: (heading, [lines]).
_SUMMARY: list[tuple[str, list[str]]] = []


def _run(script: str, env: dict[str, str], *, label: str = "",
         summarize: bool = True) -> int:
    """Run one phase script, streaming its output. Returns its exit code.

    The child runs unbuffered so a job killed by the runner's time limit still
    shows how far it got (a buffered child lost its whole report when the
    SanMar update leg hit the 350-minute cap on 2026-11-17 and 2026-11-18).
    """
    path = ROOT / script
    if not path.exists():
        print(f"  !! {script} not found -- skipping", flush=True)
        return 0
    t0 = time.monotonic()
    print(f"\n--- {script}{label}", flush=True)
    child_env = {**env, "PYTHONUNBUFFERED": "1"}
    kept: list[str] = []
    detail = 0
    proc = subprocess.Popen(
        [sys.executable, str(path)], env=child_env, cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        text = line.rstrip()
        if "Retryable NetSuite response" in text or not _SUMMARY_LINE.search(text):
            continue
        if "failures: " in text or text.startswith(("WARNING", "NOTE")):
            kept.append(text.strip())
        elif detail < SUMMARY_MAX_DETAIL:
            detail += 1
            kept.append(text.strip())
    rc = proc.wait()
    print(f"--- {script} finished rc={rc} "
          f"in {time.monotonic() - t0:,.1f}s", flush=True)
    if summarize:
        if rc:
            kept.append(f"exit code {rc}")
        _SUMMARY.append((f"{script}{label}", kept))
    return rc


#: Update-phase scripts that set no Base Price or cost of their own, so they
#: need no price guard check pass. vendor_line_price_sync copies the cost the
#: guarded script just wrote (or left alone).
NO_PRICE_GUARD = {"scripts/vendor_line_price_sync.py"}


def _guard_enabled(env: dict[str, str]) -> bool:
    return env.get("PRICE_GUARD", "").strip().lower() in ("on", "true", "1")


def _is_dry(env: dict[str, str]) -> bool:
    return env.get("SYNC_DRY_RUN", "true").strip().lower() in ("true", "1", "yes")


#: Settings the scripts read from ``.env`` that this runner must see too.
_RUN_MODE_KEYS = ("SYNC_DRY_RUN", "NETSUITE_READ_ONLY")


def load_run_mode(env: dict[str, str], dotenv_path: str | None = None) -> None:
    """Copy the run mode from ``.env`` into ``env`` where the process has none.

    The workflows write SYNC_DRY_RUN to ``.env``, which the scripts load but
    this runner did not. On the first live production update (2026-11-21) the
    runner therefore took a live run for a dry run, ran one pass, and the
    price hold was never applied: prices were written on 25 items.
    """
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - dotenv is a hard dependency
        return
    values = dotenv_values(dotenv_path) if dotenv_path else dotenv_values()
    for key in _RUN_MODE_KEYS:
        if key not in env and values.get(key) is not None:
            env[key] = str(values[key])


def _run_update(script: str, env: dict[str, str], *, label: str = "") -> int:
    """Run an update script behind the price guard (see price_guard.py).

    Guard off: one plain run. Guard on: a read-only dry pass collects how far
    Base Price and Purchase Price would move; if too many move too far the
    real pass runs with price and cost writes held. A run that is itself a dry
    run needs only the one pass -- it reports the verdict and writes nothing.
    """
    if not _guard_enabled(env):
        return _run(script, env, label=label)

    fd, report_path = tempfile.mkstemp(prefix="price_guard_", suffix=".json")
    os.close(fd)
    os.unlink(report_path)
    probe_env = {**env, "PRICE_GUARD_REPORT": report_path}
    probe_env.pop("PRICE_GUARD_HOLD", None)
    # Only a run that is known to be dry takes the single pass. With the mode
    # unknown, do the check pass and the real pass: if the run turns out to be
    # dry the second pass writes nothing, and if it is live the hold applies.
    dry = "SYNC_DRY_RUN" in env and _is_dry(env)
    if dry:
        rc = _run(script, probe_env, label=label)
    else:
        probe_env.update({"SYNC_DRY_RUN": "true", "NETSUITE_READ_ONLY": "true"})
        rc = _run(script, probe_env, label=f"{label} (price guard check, no writes)",
                  summarize=False)
    try:
        report = json.loads(Path(report_path).read_text())
    except (OSError, ValueError):
        report = None
    finally:
        Path(report_path).unlink(missing_ok=True)

    lines: list[str] = []
    hold = False
    if report is None:
        # No verdict is not a pass. Hold prices rather than write them blind.
        hold = True
        lines.append("PRICE HOLD: the price guard check produced no report "
                     f"(exit code {rc}); Base Price and Purchase Price are "
                     "held for this vendor.")
    else:
        lines += [f"PRICE GUARD {x}" for x in price_guard.describe(report)]
        over = price_guard.tripped(report)
        released = env.get("PRICE_GUARD_OVERRIDE", "").strip() == "RELEASE"
        if over and released:
            lines.append("PRICE GUARD over the limit for "
                         f"{', '.join(over)}, RELEASED by override: prices "
                         "are written.")
        elif over:
            hold = True
            lines.append(
                f"PRICE HOLD: more than {price_guard.MAX_SHARE:.0%} of "
                f"{' and '.join(over)} values move more than "
                f"{price_guard.MOVE_PCT:.0%}. Base Price and Purchase Price "
                "are held for this vendor; every other field is still "
                "written. Review, then run again with the override to release.")
    for line in lines:
        print(line, flush=True)
    _SUMMARY.append((f"{script}{label} price guard", lines))
    if dry:
        return rc
    if hold:
        # Set on the vendor's own env, not a copy, so the scripts that follow
        # in this phase (vendor_line_price_sync) see the hold too.
        env["PRICE_GUARD_HOLD"] = "1"
    return _run(script, env, label=label)


def write_summary(vendor: str, phases: tuple[str, ...], worst: int) -> None:
    """Write what this run added, changed, skipped and held.

    Goes to the GitHub run summary page and, when RUN_SUMMARY_FILE is set, to
    that file so the workflow can post it to the day's summary issue.
    """
    seen = dict(os.environ)
    load_run_mode(seen)
    mode = "dry run (nothing written)" if _is_dry(seen) else "live"
    out = [f"### {vendor}: {', '.join(phases)} ({mode})", ""]
    for heading, lines in _SUMMARY:
        out.append(f"**{heading}**")
        out += [f"- {x}" for x in lines] or ["- no totals line was printed"]
        out.append("")
    if worst:
        out.append(f"Worst exit code: {worst}")
    text = "\n".join(out) + "\n"
    for target in (os.environ.get("GITHUB_STEP_SUMMARY"),
                   os.environ.get("RUN_SUMMARY_FILE")):
        if not target:
            continue
        try:
            with open(target, "a", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            print(f"could not write summary to {target}: {exc}", flush=True)


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
    load_run_mode(env)
    env.setdefault("CREATE_MAX_STYLES", DEFAULT_CREATE_MAX_STYLES)
    budget = float(env.get("LEG_TIME_BUDGET_MIN", "0") or "0")
    if budget and not env.get("LEG_DEADLINE_EPOCH"):
        # One deadline for every script in this leg (run_status.leg_deadline).
        env["LEG_DEADLINE_EPOCH"] = f"{time.time() + budget * 60:.0f}"
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
            if vendor == "dcos":
                # One pass per supplier. The chain's dcos leg used to run
                # only the single DCOS_SUPPLIER default (champro), so TCK /
                # Cap America / Mizuno never had their feed heartbeats
                # restamped -- lifecycle saw all three at 100% stale every
                # night and only the circuit breaker kept their items alive
                # (dry run 31367785465, live run 31457843380).
                for supplier in DCOS_SUPPLIERS:
                    print(f"  -- supplier: {supplier}", flush=True)
                    worst = max(worst, _run(
                        script, {**env, "DCOS_SUPPLIER": supplier},
                        label=f" [{supplier}]"))
            elif phase == "update" and script not in NO_PRICE_GUARD:
                worst = max(worst, _run_update(script, env))
            else:
                worst = max(worst, _run(script, env))
    print(f"\n=== VENDOR {vendor} complete (worst rc={worst})", flush=True)
    write_summary(vendor, phases, worst)
    _SUMMARY.clear()
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
    valid = PHASES + ON_REQUEST_PHASES
    bad = [p for p in phases if p not in valid]
    if bad:
        print(f"unknown phase(s): {bad}; valid: {', '.join(valid)}")
        return 1

    vendors = VENDOR_ORDER if args.vendor == "all" else [args.vendor]
    worst = 0
    for vendor in vendors:
        worst = max(worst, run_vendor(vendor, phases))
    return worst


if __name__ == "__main__":
    raise SystemExit(main())

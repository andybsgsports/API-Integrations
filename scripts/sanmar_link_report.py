"""Sanmar link report: which existing NetSuite items match a feed SKU, and why not.

READ-ONLY. This script has no write call in it: it reads the Sanmar catalog
and NetSuite (SuiteQL only), prints the report, and writes local CSVs for the
workflow to attach. It is the look-before-you-link step for an account whose
Sanmar items carry no UPC and no ``SANMAR-<key>`` external id yet (production,
2026-11-20): until items are linked, the update phase matches nothing.

What it answers:

* how many existing Sanmar items match exactly one feed size and colour;
* how many do not, grouped by reason (colour the feed does not list for the
  style, size it does not list, item is inactive);
* how many feed SKUs are simply new (style, colour or size not in NetSuite);
* whether every matched item really is a Sanmar item (has a Sanmar vendor
  line) -- the match is by Vendor Name/Code, and another vendor can share a
  style number;
* which matches are doubtful (more than one NetSuite item fits one feed SKU,
  or two feed SKUs landed on one item).

Env knobs: ``RECONCILE_STYLE_LIMIT`` (0 = all styles; small N for a sample).
"""

from __future__ import annotations

import csv
import os
from collections import Counter
from pathlib import Path

from pricing_ownership import VENDOR_SANMAR

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.adopt import (
    OptionMaps,
    ReconcileReport,
    match_existing,
)
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_styles
from sanmar_netsuite.sanmar.sftp_client import SanMarSftp

LABEL = "LINK REPORT"
ID_WINDOW = 50_000
TOP = 15


def sanmar_vendor_items(client) -> set[str] | None:
    """Ids of every item with a Sanmar vendor line; None if unreadable."""
    try:
        top = client.suiteql("SELECT MAX(id) AS m FROM item")
        max_id = int((top[0].get("m") if top else 0) or 0)
        out: set[str] = set()
        lo = 0
        while lo <= max_id:
            hi = lo + ID_WINDOW
            for r in client.suiteql(
                f"SELECT DISTINCT iv.item AS item FROM itemvendor iv "
                f"WHERE iv.vendor = {VENDOR_SANMAR} "
                f"AND iv.item > {lo} AND iv.item <= {hi}"
            ):
                out.add(str(r["item"]))
            lo = hi
        return out
    except Exception as exc:  # noqa: BLE001 - report the gap, do not fail
        print(f"NOTE: {LABEL}: could not read vendor lines ({str(exc)[:120]}); "
              "the Sanmar-vendor check is skipped")
        return None


def analyse(report: ReconcileReport, options: OptionMaps,
            vendor_items: set[str] | None) -> dict:
    """Turn the raw match into the counts and reasons the report prints."""
    color_name = {vid: (name or abbr) for vid, (name, abbr)
                  in options.color_info.items()}
    size_name = {vid: name.title() for name, ids in options.size_by_name.items()
                 for vid in ids}

    # Two feed SKUs on one item: only the first could be linked.
    seen: dict[str, str] = {}
    double: list[tuple[str, str, str]] = []
    for row in report.matched:
        first = seen.setdefault(row.ns_id, row.unique_key)
        if first != row.unique_key:
            double.append((row.ns_id, first, row.unique_key))
    doubled_items = {d[0] for d in double}
    ambiguous_items = {ids[0] for ids in report.ambiguous.values()}

    clean: list[str] = []
    not_sanmar: list[str] = []
    for ns_id in seen:
        if ns_id in doubled_items or ns_id in ambiguous_items:
            continue
        if vendor_items is not None and ns_id not in vendor_items:
            not_sanmar.append(ns_id)
            continue
        clean.append(ns_id)

    by_style_colors: dict[str, set[str]] = {}
    by_style_sizes: dict[str, set[str]] = {}
    for item in report.claimed_items.values():
        by_style_colors.setdefault(item.style, set()).add(item.color_id)
        by_style_sizes.setdefault(item.style, set()).add(item.size_id)

    reasons: Counter[str] = Counter()
    colors: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    unclaimed_rows = []
    for item in report.unclaimed:
        if item.inactive:
            reason = "inactive in NetSuite"
        elif not (item.color_id and item.size_id):
            reason = "no BSG colour or size set on the item"
        elif item.color_id not in by_style_colors.get(item.style, set()):
            reason = "colour not matched to a feed colour for this style"
            colors[color_name.get(item.color_id, item.color_id)] += 1
        elif item.size_id not in by_style_sizes.get(item.style, set()):
            reason = "size not matched to a feed size for this style"
            sizes[size_name.get(item.size_id, item.size_id)] += 1
        else:
            reason = "this colour and size pair is not in the feed"
        reasons[reason] += 1
        unclaimed_rows.append((item, reason))

    missing = set(report.missing_styles)
    new_style = sum(1 for r in report.unmatched if r.style in missing)
    return {
        "clean": clean, "not_sanmar": not_sanmar, "double": double,
        "reasons": reasons, "colors": colors, "sizes": sizes,
        "unclaimed_rows": unclaimed_rows, "color_name": color_name,
        "size_name": size_name, "seen": seen,
        "feed_new_style": new_style,
        "feed_new_under_existing": len(report.unmatched) - new_style,
        "have_upc": sum(1 for i in report.claimed_items.values() if i.upc),
        "no_gtin": sum(1 for r in report.matched if not r.gtin),
    }


def describe(report: ReconcileReport, a: dict,
             vendor_items: set[str] | None) -> list[str]:
    """The report lines, each prefixed so the run summary keeps them."""
    total_ns = report.ns_children_total
    out = [
        f"existing NetSuite items under Sanmar style numbers: {total_ns:,}",
        f"matched to a feed size and colour: {report.ns_children_claimed:,}",
        f"  clean, ready to link (one item, one feed SKU"
        + (", Sanmar vendor line present" if vendor_items is not None else "")
        + f"): {len(a['clean']):,}",
        f"  matched but no Sanmar vendor line (may be another vendor's "
        f"item sharing the style number): {len(a['not_sanmar']):,}"
        if vendor_items is not None else
        "  Sanmar vendor line check: not available this run",
        f"  more than one NetSuite item fits the same feed SKU: "
        f"{len(report.ambiguous):,}",
        f"  two feed SKUs landed on the same item: {len(a['double']):,}",
        f"  matched items that already have a UPC: {a['have_upc']:,}",
        f"  matched feed SKUs with no UPC in the feed: {a['no_gtin']:,}",
        f"not matched: {len(report.unclaimed):,}",
    ]
    out += [f"  {reason}: {n:,}" for reason, n in a["reasons"].most_common()]
    if a["colors"]:
        out.append("  unmatched colours, most items first: " + "; ".join(
            f"{name} ({n:,})" for name, n in a["colors"].most_common(TOP)))
    if a["sizes"]:
        out.append("  unmatched sizes, most items first: " + "; ".join(
            f"{name} ({n:,})" for name, n in a["sizes"].most_common(TOP)))
    out += [
        f"feed SKUs with no NetSuite item (the create phase adds these): "
        f"{len(report.unmatched):,}",
        f"  in styles NetSuite does not have: {a['feed_new_style']:,}",
        f"  new colours or sizes under existing styles: "
        f"{a['feed_new_under_existing']:,}",
        f"styles with more than one parent-like item: "
        f"{len(report.dup_parents):,}",
    ]
    return [f"{LABEL}: {line}" for line in out]


def write_csvs(report: ReconcileReport, a: dict,
               vendor_items: set[str] | None, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    clean = set(a["clean"])
    not_sanmar = set(a["not_sanmar"])
    paths = []

    p = out_dir / "sanmar_link_matched.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Style", "Feed Color", "Feed Size", "Feed UPC",
                    "Sanmar Unique Key", "NetSuite Item Id", "NetSuite Item",
                    "Current UPC", "Match Method", "Status"])
        for r in report.matched:
            item = report.claimed_items.get(r.ns_id)
            if a["seen"].get(r.ns_id) != r.unique_key:
                status = "Second feed SKU on the same item"
            elif r.unique_key in report.ambiguous:
                status = "More than one NetSuite item fits"
            elif r.ns_id in not_sanmar:
                status = "No Sanmar vendor line"
            elif r.ns_id in clean:
                status = "Clean"
            else:
                status = "Review"
            w.writerow([r.style, r.color_name, r.size, r.gtin, r.unique_key,
                        r.ns_id, item.itemid if item else "",
                        item.upc if item else "", r.method, status])
    paths.append(p)

    p = out_dir / "sanmar_link_unmatched_netsuite.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Style", "NetSuite Item Id", "NetSuite Item", "BSG Color",
                    "BSG Size", "Inactive", "Has Sanmar Vendor Line", "Reason"])
        for item, reason in a["unclaimed_rows"]:
            w.writerow([
                item.style, item.ns_id, item.itemid,
                a["color_name"].get(item.color_id, item.color_id),
                a["size_name"].get(item.size_id, item.size_id),
                "Yes" if item.inactive else "No",
                "" if vendor_items is None
                else ("Yes" if item.ns_id in vendor_items else "No"),
                reason])
    paths.append(p)
    return paths


def main() -> int:
    cfg = get_config()
    limit = int(os.environ.get("RECONCILE_STYLE_LIMIT", "0") or "0")

    catalog = Path(cfg.sftp.download_dir) / C.FILE_SDL_N
    if not catalog.exists():
        catalog = SanMarSftp(cfg.sftp).download(C.FILE_SDL_N)
    styles = parse_styles(catalog)
    print(f"Parsed {len(styles)} styles from {catalog}"
          + (f" (checking the first {limit})" if limit else ""))

    client = NetSuiteClient(cfg.netsuite)
    report = match_existing(client, styles, style_limit=limit)
    print("\n" + report.summary() + "\n")

    options = OptionMaps(client)
    vendor_items = sanmar_vendor_items(client)
    a = analyse(report, options, vendor_items)
    for line in describe(report, a, vendor_items):
        print(line)
    for path in write_csvs(report, a, vendor_items, Path("data")):
        print(f"wrote {path}")
    print(f"{LABEL}: read-only, nothing was written to NetSuite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

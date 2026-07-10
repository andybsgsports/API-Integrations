"""Read-only SanMar → NetSuite reconciliation report (runs on CI).

Downloads the SanMar SDL_N catalog, matches every SKU to an existing NetSuite
item by Vendor Name/Code (style) + color + size, and reports the match rate.
Makes **no writes** — it only reads NetSuite via SuiteQL and writes local
CSVs (uploaded as workflow artifacts) so we can plan the UPC / external-id
back-fill and the color-list full-name renames.

Env knobs: ``RECONCILE_STYLE_LIMIT`` (0 = all styles; small N for a quick sample).
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.adopt import match_existing
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_styles
from sanmar_netsuite.sanmar.sftp_client import SanMarSftp


def main() -> int:
    cfg = get_config()
    limit = int(os.environ.get("RECONCILE_STYLE_LIMIT", "0") or "0")

    catalog = Path(cfg.sftp.download_dir) / C.FILE_SDL_N
    if not catalog.exists():
        catalog = SanMarSftp(cfg.sftp).download(C.FILE_SDL_N)
    styles = parse_styles(catalog)
    print(f"Parsed {len(styles)} styles from {catalog}")

    client = NetSuiteClient(cfg.netsuite)
    report = match_existing(client, styles, style_limit=limit)

    print("\n" + report.summary())

    out = Path("data/reconcile_report.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["unique_key", "style", "color_name", "mainframe_color", "size",
             "gtin", "ns_id", "method"]
        )
        for r in report.rows:
            w.writerow(
                [r.unique_key, r.style, r.color_name, r.mainframe_color, r.size,
                 r.gtin, r.ns_id or "", r.method]
            )
    print(f"\nWrote mapping -> {out} ({len(report.rows)} rows)")

    if report.color_renames:
        plan = Path("data/color_rename_plan.csv")
        with plan.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["color_value_id", "current_name", "new_full_name"])
            for vid, (cur, new) in sorted(report.color_renames.items()):
                w.writerow([vid, cur, new])
        print(f"Wrote color rename plan -> {plan} ({len(report.color_renames)} values)")
        for vid, (cur, new) in list(sorted(report.color_renames.items()))[:10]:
            print(f"  {vid}: '{cur}' -> '{new}'")

    if report.unmatched:
        print("\nSample unmatched SKUs (style | full color (mainframe) | size):")
        for r in report.unmatched[:15]:
            print(f"  {r.style} | {r.color_name} ({r.mainframe_color}) | {r.size}")
    if report.dup_parents:
        print(f"\n{len(report.dup_parents)} styles have duplicate parents; first 10:")
        for style, ids in list(report.dup_parents.items())[:10]:
            print(f"  {style}: ids {ids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

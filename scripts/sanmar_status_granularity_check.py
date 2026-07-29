"""Do we ALREADY have per-SKU product status in NetSuite?

The SanMar web-service probe found styles whose SKUs carry mixed statuses
(S500T, EB200, 64000L) and that looked like something only the web service
could give us -- which would have justified ~1,700 extra calls a night.

But the SDL feed is one row per SKU, each row carries its own PRODUCTSTATUS,
the parser reads it per row (parsers.py: product_status=row.get(...)) and the
writer sends it per SKU (sanmar_field_update.py: put("custitem_sanmar_status",
sku.product_status)). So the data should already be in NetSuite at SKU
granularity, and the web service would add nothing.

This checks that against the account instead of trusting the read: for the
styles the web service reported as mixed, group NetSuite's stored status by
style and show the split. Mixed statuses here mean the FTP path already
delivers per-SKU accuracy and the extra calls are unnecessary.

Read-only.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict

from sanmar_field_update import _dl

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_styles

# Styles the web service reported as carrying BOTH Active and Discontinued.
MIXED_STYLES = [s.strip() for s in os.environ.get(
    "GRANULARITY_STYLES", "S500T,EB200,64000L"
).split(",") if s.strip()]


def main() -> int:
    cfg = get_config()

    # FULL feed scan, not a sample. A 60-style probe found only Active /
    # Discontinued / New, but a rare value ("Discontinued Soon", "Closeout",
    # anything else) would hide in a sample that size. Every SKU gets counted
    # so the vocabulary is settled once.
    print("FULL SanMar feed: every distinct PRODUCTSTATUS value")
    print("=" * 62)
    styles = parse_styles(_dl(cfg, C.FILE_SDL_N))
    feed_vocab: Counter[str] = Counter()
    feed_styles: dict[str, set[str]] = defaultdict(set)
    for s in styles:
        for sku in s.skus:
            value = str(sku.product_status or "").strip() or "(blank)"
            feed_vocab[value] += 1
            feed_styles[s.style].add(value)
    total_skus = sum(feed_vocab.values())
    for value, n in feed_vocab.most_common():
        print(f"  {value!r:<24} {n:>7,} SKU(s)  ({100.0 * n / total_skus:.2f}%)")
    print(f"  -- {total_skus:,} SKUs across {len(styles):,} styles")

    interesting = [v for v in feed_vocab
                   if v.lower() not in ("active", "new", "regular", "(blank)")]
    print(f"\n  non-Active/New/Regular values: {interesting or 'NONE'}")
    feed_mixed = {k: v for k, v in feed_styles.items() if len(v) > 1}
    print(f"  styles whose SKUs differ in status: {len(feed_mixed):,} of "
          f"{len(feed_styles):,} ({100.0 * len(feed_mixed) / max(1, len(feed_styles)):.1f}%)")

    print()
    client = NetSuiteClient(cfg.netsuite)

    print("Account-wide custitem_sanmar_status distribution")
    print("=" * 62)
    rows = client.suiteql(
        "SELECT custitem_sanmar_status AS st, COUNT(*) AS n FROM item "
        "WHERE custitem_sanmar_unique_key IS NOT NULL "
        "GROUP BY custitem_sanmar_status"
    )
    for r in sorted(rows, key=lambda x: -int(x.get("n") or 0)):
        print(f"  {str(r.get('st'))!r:<18} {int(r.get('n') or 0):>7,} item(s)")

    print()
    print("Per-style split for the styles the web service called MIXED")
    print("=" * 62)
    mixed_confirmed = 0
    for style in MIXED_STYLES:
        detail = client.suiteql(
            "SELECT custitem_sanmar_status AS st, itemid FROM item "
            f"WHERE custitem_sanmar_style = '{_sql_escape(style)}'"
        )
        if not detail:
            print(f"  {style}: no items in NetSuite")
            continue
        counts = Counter(str(d.get("st") or "(blank)") for d in detail)
        mixed = len(counts) > 1
        mixed_confirmed += 1 if mixed else 0
        flag = "  <-- MIXED, per-SKU accuracy already present" if mixed else ""
        print(f"  {style}: {dict(counts)}{flag}")

    # Broader sweep: how many styles overall carry more than one status?
    print()
    print("How widespread is per-SKU variation, account-wide?")
    print("=" * 62)
    pairs = client.suiteql(
        "SELECT DISTINCT custitem_sanmar_style AS sty, "
        "custitem_sanmar_status AS st FROM item "
        "WHERE custitem_sanmar_unique_key IS NOT NULL"
    )
    by_style: dict[str, set[str]] = defaultdict(set)
    for p in pairs:
        by_style[str(p.get("sty"))].add(str(p.get("st") or "(blank)"))
    multi = {s: v for s, v in by_style.items() if len(v) > 1}
    print(f"  styles with >1 distinct status: {len(multi):,} of {len(by_style):,} "
          f"({100.0 * len(multi) / max(1, len(by_style)):.1f}%)")
    for style, vals in list(multi.items())[:8]:
        print(f"    {style}: {sorted(vals)}")

    print()
    if mixed_confirmed or multi:
        print("CONCLUSION: NetSuite already holds per-SKU status from the FTP "
              "feed.\nThe web-service per-SKU sync would duplicate it -- the "
              "~1,700 nightly\ncalls are unnecessary.")
    else:
        print("CONCLUSION: no per-SKU variation found; the web service would "
              "add granularity.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

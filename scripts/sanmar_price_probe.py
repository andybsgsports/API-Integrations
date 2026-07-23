"""Dump every SanMar price field for one style, from BOTH feeds.

Answers "why is the Purchase Price (cost) X?" by showing, per SKU, exactly
what the SDL feed and the DIP feed carry -- and what our cost logic
(effective_cost) computes from them. Read-only; no NetSuite writes.

    SANMAR_PROBE_STYLE=DM130L python scripts/sanmar_price_probe.py

Optional filters: SANMAR_PROBE_COLOR, SANMAR_PROBE_SIZE (case-insensitive
substring match on the SDL colour name / size).
"""

from __future__ import annotations

import csv
import os
from datetime import date

from sanmar_field_update import _dl, effective_cost

from sanmar_netsuite.config import get_config
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_inventory, parse_styles


def _f(v) -> str:
    return "-" if v is None else f"{float(v):.2f}"


def _dump_raw_headers(cfg, styles_wanted: set[str]) -> None:
    """Print the full raw column list of the SDL and EPDD product feeds, plus one
    raw row for a wanted style -- reveals bullet/feature/keyword columns our
    reader (which keeps only mapped columns) would otherwise discard."""
    for fname in (C.FILE_SDL_N, C.FILE_EPDD):
        print(f"\n########## RAW HEADERS: {fname} ##########")
        try:
            path = _dl(cfg, fname)
        except Exception as exc:  # noqa: BLE001 - feed may not be provisioned
            print(f"  (could not download {fname}: {str(exc)[:160]})")
            continue
        with open(path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            cols = reader.fieldnames or []
            print(f"  {len(cols)} columns:")
            for c_ in cols:
                print(f"    - {c_!r}")
            # Find the style column and print one matching row in full.
            style_col = next(
                (c_ for c_ in cols if "".join(ch for ch in c_.upper()
                 if ch.isalnum()) in ("STYLE", "STYLE#", "STYLENUMBER")),
                None,
            )
            if not style_col:
                continue
            for row in reader:
                if (row.get(style_col) or "").strip().upper() in styles_wanted:
                    print(f"\n  first raw row for style {row.get(style_col)!r}:")
                    for k, v in row.items():
                        val = (v or "")
                        print(f"    {k!r}: {val[:300]!r}")
                    break


def main() -> int:
    styles_wanted = {
        s.strip().upper()
        for s in (os.environ.get("SANMAR_PROBE_STYLE") or "DM130L").split(",")
        if s.strip()
    }
    color_filter = (os.environ.get("SANMAR_PROBE_COLOR") or "").strip().lower()
    size_filter = (os.environ.get("SANMAR_PROBE_SIZE") or "").strip().lower()
    # Title substring (case-insensitive) lets us find a style by name when we
    # don't know its number. A style is probed if its number is in STYLE *or*
    # its title contains this substring.
    title_filter = (os.environ.get("SANMAR_PROBE_TITLE") or "").strip().lower()

    cfg = get_config()
    if (os.environ.get("SANMAR_PROBE_RAWHEADERS") or "").lower() == "true":
        _dump_raw_headers(cfg, styles_wanted)
        return 0
    styles = parse_styles(_dl(cfg, C.FILE_SDL_N))
    inventory = parse_inventory(_dl(cfg, C.FILE_DIP))
    dip_by_key = {rec.unique_key: rec for rec in inventory}
    today = date.today()

    print(f"probe styles: {sorted(styles_wanted)}  (today={today})")
    print(
        "SDL = SanMar_SDL_N.csv (catalog), DIP = sanmar_dip.txt (daily inv+pricing)\n"
        "cost basis today = SDL piece_price; sale from DIP each_sale_price\n"
    )
    hdr = (
        f"{'color':<18} {'size':<9} {'gtin':<15} "
        f"{'SDLpiece':>8} {'SDLmsrp':>8} {'SDLmap':>7} {'SDLcase':>8} "
        f"{'DIPpiece':>8} {'DIPsale':>8} {'saleStart':>10} {'saleEnd':>10} "
        f"{'->cost':>7} {'onSale':>6}"
    )
    print(hdr)
    print("-" * len(hdr))

    matched = 0
    for style in styles:
        by_style = style.style.upper() in styles_wanted
        by_title = bool(title_filter) and title_filter in (style.title or "").lower()
        if not (by_style or by_title):
            continue
        # Dump the raw title + description so we can see exactly what the feed
        # carries for Store Display Name / Store Description (repr reveals
        # newlines and bullet characters).
        print(f"\n=== {style.style}  TITLE / DESCRIPTION (raw from SDL) ===")
        print(f"TITLE      : {style.title!r}")
        print(f"DESCRIPTION: {style.description!r}")
        print(f"DESCRIPTION (rendered):\n{style.description}\n")
        for sku in style.skus:
            if color_filter and color_filter not in (sku.color_name or "").lower():
                continue
            if size_filter and size_filter not in (sku.size or "").lower():
                continue
            matched += 1
            dip = dip_by_key.get(sku.unique_key)
            dip_piece = dip.piece_price if dip else None
            dip_sale = dip.each_sale_price if dip else None
            s_start = dip.sale_start if dip else ""
            s_end = dip.sale_end if dip else ""
            cost, on_sale = effective_cost(
                sku.piece_price, dip_sale, s_start, s_end, today
            )
            print(
                f"{(sku.color_name or ''):<18.18} {(sku.size or ''):<9.9} "
                f"{(sku.gtin or ''):<15} "
                f"{_f(sku.piece_price):>8} {_f(sku.msrp):>8} {_f(sku.map_price):>7} "
                f"{_f(sku.case_price):>8} "
                f"{_f(dip_piece):>8} {_f(dip_sale):>8} {s_start:>10.10} {s_end:>10.10} "
                f"{_f(cost):>7} {('YES' if on_sale else 'no'):>6}"
            )
    print(f"\nmatched {matched} SKU(s)")
    print(
        "\nread: if SDLpiece is the 7.20-ish number but DIPpiece / program price is "
        "lower, the cost basis should be the DIP account price, not the SDL piece price."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

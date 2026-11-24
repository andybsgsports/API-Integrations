"""Export the vendors' own colour names for a list of styles (read-only).

For the BSG colour list cleanup (Andy, 2026-11-18): the list still has
abbreviations from the old system (Dgh, Mgrn, Trnv...). The correct name is
whatever the vendor calls that colour on that style, so this writes, for each
style asked for, every colour the SanMar and S&S catalogues list for it --
the full name and the vendor's own short code beside it (SanMar's mainframe
colour, S&S's colour code). Many of the old abbreviations are those codes.

Touches no NetSuite record: it reads the two vendor catalogues and writes
``data/vendor_color_map.csv`` (source, style, brand, color_name, short_code,
status).

``COLOR_MAP_STYLES`` is a comma-separated list of style numbers; matching
ignores case.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

OUT = Path("data") / "vendor_color_map.csv"
FIELDS = ["source", "style", "brand", "color_name", "short_code", "status"]


def wanted_styles(raw: str) -> set[str]:
    return {s.strip().upper() for s in (raw or "").split(",") if s.strip()}


def sanmar_rows(styles, wanted: set[str]) -> list[dict]:
    """One row per (style, colour) from parsed SanMar StyleRecords."""
    out: dict[tuple[str, str], dict] = {}
    for st in styles:
        if st.style.upper() not in wanted:
            continue
        for sku in st.skus:
            key = (st.style.upper(), sku.color_name.upper())
            out.setdefault(key, {
                "source": "SanMar", "style": st.style, "brand": st.brand,
                "color_name": sku.color_name,
                "short_code": sku.mainframe_color,
                "status": st.product_status,
            })
    return list(out.values())


def ss_rows(products: list[dict], wanted: set[str]) -> list[dict]:
    """One row per (style, colour) from the downloaded S&S products."""
    out: dict[tuple[str, str], dict] = {}
    for p in products:
        style = str(p.get("style_name") or "").strip()
        if style.upper() not in wanted:
            continue
        color = str(p.get("color_name") or "").strip()
        out.setdefault((style.upper(), color.upper()), {
            "source": "S&S", "style": style,
            "brand": str(p.get("brand_name") or "").strip(),
            "color_name": color,
            "short_code": str(p.get("color_code") or "").strip(),
            "status": "",
        })
    return list(out.values())


def main() -> int:
    wanted = wanted_styles(os.environ.get("COLOR_MAP_STYLES", ""))
    if not wanted:
        print("COLOR_MAP_STYLES is empty -- nothing to export")
        return 1
    rows: list[dict] = []

    try:
        from sanmar_netsuite.config import SftpConfig
        from sanmar_netsuite.sanmar import constants as C
        from sanmar_netsuite.sanmar.parsers import parse_styles
        from sanmar_netsuite.sanmar.sftp_client import SanMarSftp
        sftp = SftpConfig.from_env()
        path = Path(sftp.download_dir) / C.FILE_SDL_N
        if not path.exists():
            path = SanMarSftp(sftp).download(C.FILE_SDL_N)
        found = sanmar_rows(parse_styles(path), wanted)
        print(f"SanMar: {len(found)} style/colour row(s)")
        rows += found
    except Exception as exc:  # noqa: BLE001 - one vendor down must not lose the other
        print(f"WARNING: SanMar catalogue not read: {str(exc)[:200]}")

    try:
        from ss_activewear_netsuite.config import get_config as ss_config
        products_file = Path(ss_config().download_dir) / "products.json"
        products = json.loads(products_file.read_text(encoding="utf-8"))
        found = ss_rows(products, wanted)
        print(f"S&S: {len(found)} style/colour row(s)")
        rows += found
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: S&S catalogue not read: {str(exc)[:200]}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["style"].upper(), r["source"],
                                                r["color_name"])))
    got = {r["style"].upper() for r in rows}
    missing = sorted(wanted - got)
    print(f"wrote {len(rows)} row(s) for {len(got)} of {len(wanted)} style(s) "
          f"to {OUT}")
    if missing:
        print("styles in neither catalogue (discontinued, or another "
              "vendor's): " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

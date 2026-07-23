"""List every distinct SanMar (category, subcategory) pair in the feed, with
SKU counts, and flag which ones aren't mapped to a NetSuite Class in
``CATEGORY_TO_CLASS``.

Read-only, SFTP only (no NetSuite calls) -- used to build a complete category
mapping so Department/Class can be corrected across the whole catalog, not just
the dozen categories currently covered.
"""

from __future__ import annotations

from collections import Counter

from sanmar_netsuite.config import get_config
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_styles
from sanmar_netsuite.sanmar.sftp_client import SanMarSftp
from sanmar_netsuite.transform.csv_export import CATEGORY_TO_CLASS


def main() -> int:
    cfg = get_config()
    path = SanMarSftp(cfg.sftp).download(C.FILE_SDL_N)
    styles = parse_styles(path)

    counts: Counter[tuple[str, str]] = Counter()
    for style in styles:
        counts[(style.category or "", style.subcategory or "")] += len(style.skus)

    print(f"styles: {len(styles):,}  distinct (category, subcategory) pairs: {len(counts)}\n")
    mapped_skus = unmapped_skus = 0
    print(f"{'MAPPED':<8} {'category':<35} {'subcategory':<30} {'skus':>8}  -> class")
    print("-" * 100)
    for (cat, sub), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        cls = CATEGORY_TO_CLASS.get(cat.strip().lower(), "")
        tag = "yes" if cls else "NO"
        if cls:
            mapped_skus += n
        else:
            unmapped_skus += n
        print(f"{tag:<8} {cat:<35.35} {sub:<30.30} {n:>8,}  -> {cls or '(unmapped)'}")

    print(f"\nSKUs with a mapped class: {mapped_skus:,}")
    print(f"SKUs with NO mapped class (would land with blank Class): {unmapped_skus:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

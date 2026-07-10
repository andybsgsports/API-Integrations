"""Match SanMar feed SKUs to already-existing NetSuite items ("adoption").

Badger's catalog was migrated from a previous system: items carry
NetSuite-internal numeric external ids (not this integration's ``SANMAR-<key>``
convention), no UPC yet, and names of the form ``STYLE-COLOR-SIZE`` using
abbreviated colors. The stable link Andy identified is the **Vendor Name/Code**
(``vendorname``), which holds the SanMar style on both parents and children.

This module matches each feed SKU to its existing NetSuite item so a one-time
back-fill can stamp the UPC and ``SANMAR-<unique_key>`` external id onto it.
Matching is:

1. **style**  → items whose ``vendorname`` equals the style, then
2. **color+size** within that style — trying the SanMar *mainframe* (abbreviated)
   color first, then the full color name, against the item's parsed name.

Everything here is read-only; it produces a report + mapping the caller can act on.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ..models import StyleRecord
from ..transform.sizes import normalize_size
from .repository import _sql_escape

# Spelled sizes the migrated catalog uses as the trailing token in item names.
# Longest-first so "2X-Large" wins over "Large" when stripping the suffix.
_KNOWN_SIZES = (
    "5X-Large", "4X-Large", "3X-Large", "2X-Large", "X-Large", "X-Small",
    "5XL", "4XL", "3XL", "2XL",
    "One Size", "Small", "Medium", "Large", "XS", "XL",
)


def split_color_size(itemid: str, style: str) -> tuple[str, str] | None:
    """Parse ``STYLE-COLOR-SIZE`` into ``(color, size)``; None if it doesn't fit.

    The parent item (``itemid == style``) and any oddly-named row return None.
    """
    prefix = f"{style}-"
    if not itemid.startswith(prefix):
        return None
    rest = itemid[len(prefix):]
    for size in sorted(_KNOWN_SIZES, key=len, reverse=True):
        if rest.endswith(f"-{size}") and len(rest) > len(size) + 1:
            return rest[: -(len(size) + 1)], size
    return None


@dataclass
class MatchRow:
    unique_key: str
    style: str
    color_name: str
    mainframe_color: str
    size: str
    gtin: str
    ns_id: str | None = None
    method: str = "unmatched"  # mainframe | fullcolor | unmatched


@dataclass
class ReconcileReport:
    rows: list[MatchRow] = field(default_factory=list)
    dup_parents: dict[str, list[str]] = field(default_factory=dict)
    missing_styles: list[str] = field(default_factory=list)  # style has no NS items

    @property
    def matched(self) -> list[MatchRow]:
        return [r for r in self.rows if r.ns_id]

    @property
    def unmatched(self) -> list[MatchRow]:
        return [r for r in self.rows if not r.ns_id]

    def summary(self) -> str:
        total = len(self.rows)
        m = len(self.matched)
        by_method: dict[str, int] = {}
        for r in self.matched:
            by_method[r.method] = by_method.get(r.method, 0) + 1
        pct = (100.0 * m / total) if total else 0.0
        lines = [
            f"reconcile: {m}/{total} SKUs matched ({pct:.1f}%)",
            f"  by mainframe color: {by_method.get('mainframe', 0)}",
            f"  by full color:      {by_method.get('fullcolor', 0)}",
            f"  unmatched:          {len(self.unmatched)}",
            f"  styles with no NS items: {len(self.missing_styles)}",
            f"  styles with duplicate parents: {len(self.dup_parents)}",
        ]
        return "\n".join(lines)


def _index_style_items(client, style: str) -> tuple[dict[tuple[str, str], list[str]], list[str]]:
    """Return ``((color,size)->[ids], parent/other ids)`` for one style's items."""
    rows = client.suiteql(
        f"SELECT id, itemid FROM item WHERE vendorname = '{_sql_escape(style)}'"
    )
    index: dict[tuple[str, str], list[str]] = {}
    others: list[str] = []
    for r in rows:
        itemid = str(r.get("itemid", ""))
        parsed = split_color_size(itemid, style)
        if parsed is None:
            others.append(str(r["id"]))
            continue
        color, size = parsed
        index.setdefault((color.strip().lower(), size.strip().lower()), []).append(str(r["id"]))
    return index, others


def match_existing(
    client, styles: Iterable[StyleRecord], *, style_limit: int = 0
) -> ReconcileReport:
    """Match every SKU to an existing NetSuite item by vendorName + color + size."""
    report = ReconcileReport()
    style_list = list(styles)
    if style_limit:
        style_list = style_list[:style_limit]

    for style in style_list:
        index, others = _index_style_items(client, style.style)
        if not index and not others:
            report.missing_styles.append(style.style)
        if len(others) > 1:
            report.dup_parents[style.style] = others

        for sku in style.skus:
            size_n = normalize_size(sku.size)
            row = MatchRow(
                unique_key=sku.unique_key,
                style=style.style,
                color_name=sku.color_name,
                mainframe_color=sku.mainframe_color,
                size=size_n,
                gtin=sku.gtin,
            )
            for color, method in (
                (sku.mainframe_color, "mainframe"),
                (sku.color_name, "fullcolor"),
            ):
                key = (color.strip().lower(), size_n.strip().lower())
                ids = index.get(key)
                if ids:
                    row.ns_id = ids[0]
                    row.method = method
                    break
            report.rows.append(row)

    return report

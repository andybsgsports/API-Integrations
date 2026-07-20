"""Match Momentec feed SKUs to existing NetSuite items (adoption).

Mirror of :mod:`sanmar_netsuite.netsuite.adopt`, adapted to Momentec's feed:

* The style token is the ``Item_SKU`` prefix (keeps leading zeros the
  ``Parent_SKU`` column sometimes drops), looked up via ``vendorname``.
* Feed colors are full names in caps, often with a brand tag suffix —
  ``GRAPHITE (BA)`` — which is stripped before matching against the color
  list's Name/Abbreviation.
* Feed sizes may be exact list values (``S/M``, ``YTH``) or SanMar-style
  codes (``2XL``); both raw and normalized forms are tried.

No color renames are proposed: Momentec names are already full words, and
re-casing the shared list to ALL CAPS would be churn, not repair.

Read-only; produces a report + mapping for a later back-fill.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from sanmar_netsuite.netsuite.adopt import COLOR_FIELD, SIZE_FIELD, OptionMaps
from sanmar_netsuite.netsuite.repository import _sql_escape
from sanmar_netsuite.transform.sizes import normalize_size

from .models import MomentecStyle

_BRAND_TAG = re.compile(r"\s*\([A-Z]{2,3}\)\s*$")


def clean_color(color: str) -> str:
    """``GRAPHITE (BA)`` → ``GRAPHITE``; trims the brand tag and whitespace."""
    return _BRAND_TAG.sub("", (color or "").strip())


@dataclass
class MomentecMatchRow:
    item_sku: str
    style: str
    color: str
    size: str
    gtin: str
    upc: str
    ns_id: str | None = None
    method: str = "unmatched"


@dataclass
class MomentecReport:
    rows: list[MomentecMatchRow] = field(default_factory=list)
    missing_styles: list[str] = field(default_factory=list)
    dup_parents: dict[str, list[str]] = field(default_factory=dict)
    ns_children_total: int = 0
    ns_children_claimed: int = 0

    @property
    def matched(self) -> list[MomentecMatchRow]:
        return [r for r in self.rows if r.ns_id]

    def summary(self) -> str:
        total = len(self.rows)
        m = len(self.matched)
        by_method: dict[str, int] = {}
        for r in self.matched:
            by_method[r.method] = by_method.get(r.method, 0) + 1
        pct = (100.0 * m / total) if total else 0.0
        cov = (
            100.0 * self.ns_children_claimed / self.ns_children_total
            if self.ns_children_total
            else 0.0
        )
        return "\n".join(
            [
                f"momentec reconcile: {m}/{total} feed SKUs matched ({pct:.1f}%)",
                *(f"  {k}: {v}" for k, v in sorted(by_method.items())),
                f"EXISTING NetSuite child items claimed: "
                f"{self.ns_children_claimed}/{self.ns_children_total} ({cov:.1f}%)",
                f"  styles with no NS items: {len(self.missing_styles)}",
                f"  styles with duplicate parents: {len(self.dup_parents)}",
            ]
        )


def _fetch_style_items(client, style: str) -> list[dict]:
    safe = _sql_escape(style)
    try:
        return client.suiteql(
            f"SELECT id, itemid, {COLOR_FIELD} AS color, {SIZE_FIELD} AS size "
            f"FROM item WHERE vendorname = '{safe}'"
        )
    except Exception:  # noqa: BLE001 - option fields unreadable
        return client.suiteql(
            f"SELECT id, itemid FROM item WHERE vendorname = '{safe}'"
        )


def match_momentec(
    client, styles: Iterable[MomentecStyle], *, style_limit: int = 0
) -> MomentecReport:
    """Match each Momentec SKU to an existing item via option ids."""
    report = MomentecReport()
    options = OptionMaps(client)

    # Group by canonical style token (Item_SKU prefix).
    by_style: dict[str, list] = {}
    for style in styles:
        for sku in style.skus:
            token = sku.item_sku.split(".")[0]
            if token:
                by_style.setdefault(token, []).append(sku)
    tokens = sorted(by_style)
    if style_limit:
        tokens = tokens[:style_limit]

    for token in tokens:
        rows = _fetch_style_items(client, token)
        opt_index: dict[tuple[str, str], list[str]] = {}
        children: set[str] = set()
        parents: list[str] = []
        for r in rows:
            rid = str(r["id"])
            color_id = str(r.get("color") or "")
            size_id = str(r.get("size") or "")
            if color_id and size_id:
                opt_index.setdefault((color_id, size_id), []).append(rid)
                children.add(rid)
            else:
                parents.append(rid)
        if not rows:
            report.missing_styles.append(token)
        if len(parents) > 1:
            report.dup_parents[token] = parents
        report.ns_children_total += len(children)

        claimed: set[str] = set()
        for sku in by_style[token]:
            color = clean_color(sku.color)
            row = MomentecMatchRow(
                item_sku=sku.item_sku, style=token, color=sku.color,
                size=sku.size, gtin=sku.gtin, upc=sku.upc,
            )
            if options.available and opt_index:
                size_ids = list(
                    dict.fromkeys(
                        options.size_candidates(sku.size)
                        + options.size_candidates(normalize_size(sku.size))
                    )
                )
                for color_id, method in options.color_candidates(color, color):
                    hit = None
                    for size_id in size_ids:
                        hit = opt_index.get((color_id, size_id))
                        if hit:
                            break
                    if hit:
                        row.ns_id = hit[0]
                        row.method = method
                        break
            if row.ns_id:
                claimed.add(row.ns_id)
            report.rows.append(row)
        report.ns_children_claimed += len(claimed & children)

    return report

"""Match SanMar feed SKUs to already-existing NetSuite items ("adoption").

Badger's catalog was migrated from a previous system: items carry
NetSuite-internal numeric external ids (not this integration's ``SANMAR-<key>``
convention), no UPC yet, and names of the form ``STYLE-COLOR-SIZE`` using
abbreviated colors. The stable link Andy identified is the **Vendor Name/Code**
(``vendorname``), which holds the SanMar style on both parents and children.

This module matches each feed SKU to its existing NetSuite item so a one-time
back-fill can stamp the UPC and ``SANMAR-<unique_key>`` external id onto it.

Matching, per style (items found via ``vendorname``):

1. **Option ids** (primary) — each child item points at the matrix color/size
   custom lists by internal id. The feed color is resolved to list-value ids by
   the list's *Name*, its *Abbreviation*, or an initial-pairs heuristic
   (``California Blue`` → ``cabl``), and the size by the spelled-out size name.
2. **Name parse** (fallback) — ``STYLE-COLOR-SIZE`` item names matched with the
   SanMar mainframe (abbreviated) color first, then the full color name.

The report also measures the direction that matters for the back-fill: how many
of the *existing* NetSuite child items were claimed by some feed SKU.

Everything here is read-only; it produces a report + mapping the caller acts on.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ..models import StyleRecord
from ..transform.sizes import normalize_size
from .repository import _sql_escape

# BSG's matrix option lists and the item fields that reference them.
COLOR_LIST = "customlist_bsg_matrix_color"
SIZE_LIST = "customlist_bsg_matrix_size"
COLOR_FIELD = "custitem_bsg_color"
SIZE_FIELD = "custitem_bsg_size"

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


def heuristic_abbrev(full_color: str) -> str:
    """BSG-style abbreviation guess: first two letters of each word, joined.

    ``California Blue`` → ``cabl``; ``Forest Green`` → ``fogr``. Lower-cased for
    case-insensitive lookup. Single words come back as-is truncated is NOT
    attempted (whole word is a better key).
    """
    words = [w for w in full_color.split() if w]
    if len(words) < 2:
        return full_color.strip().lower()
    return "".join(w[:2] for w in words).lower()


class OptionMaps:
    """Name/abbreviation → list-value-id lookups for the color and size lists."""

    def __init__(self, client) -> None:
        self.color_by_name: dict[str, list[str]] = {}
        self.color_by_abbrev: dict[str, list[str]] = {}
        self.size_by_name: dict[str, list[str]] = {}
        self.color_info: dict[str, tuple[str, str]] = {}  # id -> (name, abbrev)
        self.available = False
        try:
            self._load(client)
            self.available = bool(self.color_by_name or self.color_by_abbrev)
        except Exception:  # noqa: BLE001 - lists unreadable -> name-parse only
            self.available = False

    def _load(self, client) -> None:
        try:
            rows = client.suiteql(f"SELECT id, name, abbreviation FROM {COLOR_LIST}")
        except Exception:  # noqa: BLE001 - abbreviation column may not project
            rows = client.suiteql(f"SELECT id, name FROM {COLOR_LIST}")
        for r in rows:
            vid = str(r["id"])
            raw_name = str(r.get("name") or "").strip()
            raw_abbrev = str(r.get("abbreviation") or "").strip()
            self.color_info[vid] = (raw_name, raw_abbrev)
            if raw_name:
                self.color_by_name.setdefault(raw_name.lower(), []).append(vid)
            if raw_abbrev:
                self.color_by_abbrev.setdefault(raw_abbrev.lower(), []).append(vid)
        for r in client.suiteql(f"SELECT id, name FROM {SIZE_LIST}"):
            name = str(r.get("name") or "").strip().lower()
            if name:
                self.size_by_name.setdefault(name, []).append(str(r["id"]))

    def color_candidates(self, full_color: str, mainframe: str) -> list[tuple[str, str]]:
        """Ordered, deduped ``(list value id, method label)`` candidates."""
        full = full_color.strip().lower()
        mf = mainframe.strip().lower()
        out: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add(ids: list[str] | None, method: str) -> None:
            for vid in ids or []:
                if vid not in seen:
                    seen.add(vid)
                    out.append((vid, method))

        add(self.color_by_name.get(full), "option:name")
        add(self.color_by_abbrev.get(mf), "option:abbrev")
        add(self.color_by_name.get(mf), "option:name")
        add(self.color_by_abbrev.get(full), "option:abbrev")
        heur = heuristic_abbrev(full_color)
        add(self.color_by_name.get(heur), "option:heur")
        add(self.color_by_abbrev.get(heur), "option:heur")
        return out

    def size_candidates(self, size_normalized: str) -> list[str]:
        return self.size_by_name.get(size_normalized.strip().lower(), [])


@dataclass
class MatchRow:
    unique_key: str
    style: str
    color_name: str
    mainframe_color: str
    size: str
    gtin: str
    ns_id: str | None = None
    method: str = "unmatched"  # option:* | mainframe | fullcolor | unmatched


@dataclass
class ReconcileReport:
    rows: list[MatchRow] = field(default_factory=list)
    dup_parents: dict[str, list[str]] = field(default_factory=dict)
    missing_styles: list[str] = field(default_factory=list)  # style has no NS items
    ns_children_total: int = 0  # existing NS child items across checked styles
    ns_children_claimed: int = 0  # ...of which some feed SKU matched
    # color list value id -> (current display name, SanMar full color name):
    # values matched via abbreviation/heuristic whose Name should become the
    # full color (renaming a list value cascades to every item using it).
    color_renames: dict[str, tuple[str, str]] = field(default_factory=dict)

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
        cov = (
            100.0 * self.ns_children_claimed / self.ns_children_total
            if self.ns_children_total
            else 0.0
        )
        lines = [
            f"reconcile: {m}/{total} feed SKUs matched ({pct:.1f}%)",
            *(f"  {meth}: {n}" for meth, n in sorted(by_method.items())),
            f"  unmatched feed SKUs: {len(self.unmatched)}",
            "",
            f"EXISTING NetSuite child items claimed: "
            f"{self.ns_children_claimed}/{self.ns_children_total} ({cov:.1f}%)"
            "   <- the back-fill coverage metric",
            f"  styles with no NS items: {len(self.missing_styles)}",
            f"  styles with duplicate parents: {len(self.dup_parents)}",
            f"  color list values to rename to full names: {len(self.color_renames)}",
        ]
        return "\n".join(lines)


def _fetch_style_items(client, style: str) -> list[dict]:
    """All items whose vendorname equals the style, with option ids if readable."""
    safe = _sql_escape(style)
    try:
        return client.suiteql(
            f"SELECT id, itemid, {COLOR_FIELD} AS color, {SIZE_FIELD} AS size "
            f"FROM item WHERE vendorname = '{safe}'"
        )
    except Exception:  # noqa: BLE001 - option fields unreadable -> names only
        return client.suiteql(
            f"SELECT id, itemid FROM item WHERE vendorname = '{safe}'"
        )


def match_existing(
    client, styles: Iterable[StyleRecord], *, style_limit: int = 0
) -> ReconcileReport:
    """Match every SKU to an existing NetSuite item (option ids, then names)."""
    report = ReconcileReport()
    style_list = list(styles)
    if style_limit:
        style_list = style_list[:style_limit]

    options = OptionMaps(client)

    for style in style_list:
        rows = _fetch_style_items(client, style.style)

        name_index: dict[tuple[str, str], list[str]] = {}
        opt_index: dict[tuple[str, str], list[str]] = {}
        children: set[str] = set()
        others: list[str] = []
        for r in rows:
            rid = str(r["id"])
            itemid = str(r.get("itemid", ""))
            color_id = str(r.get("color") or "")
            size_id = str(r.get("size") or "")
            parsed = split_color_size(itemid, style.style)
            if color_id and size_id:
                opt_index.setdefault((color_id, size_id), []).append(rid)
                children.add(rid)
            if parsed is not None:
                color, size = parsed
                name_index.setdefault(
                    (color.strip().lower(), size.strip().lower()), []
                ).append(rid)
                children.add(rid)
            elif not (color_id and size_id):
                others.append(rid)

        if not rows:
            report.missing_styles.append(style.style)
        if len(others) > 1:
            report.dup_parents[style.style] = others
        report.ns_children_total += len(children)

        claimed: set[str] = set()
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
            # 1) option-id match (robust to naming differences)
            if options.available and opt_index:
                size_ids = options.size_candidates(size_n)
                for color_id, method in options.color_candidates(
                    sku.color_name, sku.mainframe_color
                ):
                    hit = None
                    for size_id in size_ids:
                        hit = opt_index.get((color_id, size_id))
                        if hit:
                            break
                    if hit:
                        row.ns_id = hit[0]
                        row.method = method
                        # Matched via abbreviation/heuristic => the list value's
                        # Name isn't the full color yet; propose the rename.
                        cur_name, _ = options.color_info.get(color_id, ("", ""))
                        if (
                            sku.color_name.strip()
                            and cur_name.strip().lower()
                            != sku.color_name.strip().lower()
                        ):
                            report.color_renames[color_id] = (
                                cur_name,
                                sku.color_name.strip(),
                            )
                        break
            # 2) fallback: parse the item name
            if row.ns_id is None:
                for color, method in (
                    (sku.mainframe_color, "mainframe"),
                    (sku.color_name, "fullcolor"),
                ):
                    ids = name_index.get((color.strip().lower(), size_n.strip().lower()))
                    if ids:
                        row.ns_id = ids[0]
                        row.method = method
                        break
            if row.ns_id:
                claimed.add(row.ns_id)
            report.rows.append(row)

        report.ns_children_claimed += len(claimed & children)

    return report

"""Resolve — and optionally auto-create — NetSuite matrix color/size options.

A matrix-item import fails if a SanMar color or size isn't already a value in the
backing custom list, so before (or during) a load we make sure every needed
value exists, creating the ones that don't.

The two backing lists are::

    custitem_bsg_color  ->  customlist_bsg_matrix_color
    custitem_bsg_size   ->  customlist_bsg_matrix_size

Matching is **case-insensitive** against existing entries (the live lists carry
mixed casing and some duplicates); when several entries share a name we reuse
the lowest *active* internal id (retired duplicates are inactive). Sizes are spelled out
(:func:`~sanmar_netsuite.transform.sizes.normalize_size`) before lookup so the
feed's ``S``/``M``/``L`` line up with the list's ``Small``/``Medium``/``Large``.

Creation is gated by ``allow_create`` so a dry run can *report* what it would add
without writing to these shared master lists.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from ..models import StyleRecord
from ..transform.sizes import normalize_size
from .client import NetSuiteClient

log = logging.getLogger(__name__)

COLOR_LIST = "customlist_bsg_matrix_color"
SIZE_LIST = "customlist_bsg_matrix_size"


def _sql_escape(value: str) -> str:
    return value.replace("'", "''")


def normalize_option_name(name: str) -> str:
    """Canonical key for "is this the same option value?".

    Strips case and every non-alphanumeric character, so the feed's
    ``'J. Navy'`` and the list's ``'J.Navy'`` -- or ``'Black/ Red'`` and
    ``'Black/Red'`` -- collapse to one key.

    This is THE definition of option-value identity, and it lives here so the
    two places that need it cannot disagree. They did: the ensure-values
    pre-pass used this rule and correctly refused to create ``'J. Navy'`` as a
    duplicate of ``'J.Navy'``, while the create path's resolver matched only on
    exact (case-insensitive) name, failed to find it, and -- with
    ``allow_create`` on -- created the duplicate anyway. The 2026-08-04 dry run
    caught it about to happen across hundreds of colours (run 30947041147);
    it is the same failure mode as the Forest/Forrest mess.
    """
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def collect_options(styles: Iterable[StyleRecord]) -> tuple[set[str], set[str]]:
    """Return the distinct (colors, spelled-out sizes) referenced across ``styles``."""
    colors: set[str] = set()
    sizes: set[str] = set()
    for style in styles:
        for sku in style.skus:
            if sku.color_name and sku.color_name.strip():
                colors.add(sku.color_name.strip())
            size = normalize_size(sku.size)
            if size:
                sizes.add(size)
    return colors, sizes


@dataclass
class MatrixOptionResolver:
    """Look up matrix list values by name, creating them when ``allow_create``."""

    client: NetSuiteClient
    allow_create: bool = True
    _cache: dict[tuple[str, str], str] = field(default_factory=dict, init=False, repr=False)
    _norm_cache: dict[str, dict[str, tuple[str, str]]] = field(
        default_factory=dict, init=False, repr=False
    )

    def resolve(self, list_type: str, name: str) -> tuple[str | None, str]:
        """Resolve ``name`` to an internal id.

        Returns ``(internal_id, status)`` where ``status`` is one of
        ``existing`` / ``created`` / ``missing`` (``missing`` only when creation
        is disabled) / ``empty``.
        """
        name = (name or "").strip()
        if not name:
            return None, "empty"
        key = (list_type, name.casefold())
        if key in self._cache:
            return self._cache[key], "existing"
        existing = self._find(list_type, name)
        if existing is not None:
            self._cache[key] = existing
            return existing, "existing"
        # Punctuation/spacing variant of a value already on the list? Reuse it.
        # Without this the resolver creates a near-duplicate colour (see
        # normalize_option_name).
        variant = self._find_variant(list_type, name)
        if variant is not None:
            self._cache[key] = variant
            return variant, "existing"
        if not self.allow_create:
            return None, "missing"
        new_id = self.client.create_record(list_type, {"name": name})
        log.info("Created %s value %r -> internal id %s", list_type, name, new_id)
        self._cache[key] = new_id
        return new_id, "created"

    def _find(self, list_type: str, name: str) -> str | None:
        # Retired duplicate values are marked inactive (color consolidation);
        # prefer an active id so new items never point at a retired value.
        rows = self.client.suiteql(
            f"SELECT id, isinactive FROM {list_type} "
            f"WHERE LOWER(name) = LOWER('{_sql_escape(name)}') ORDER BY id"
        )
        active = [r for r in rows if str(r.get("isinactive") or "F") != "T"]
        pick = active[0] if active else (rows[0] if rows else None)
        return str(pick["id"]) if pick else None

    def _find_variant(self, list_type: str, name: str) -> str | None:
        """Id of an existing value that differs only by punctuation/spacing."""
        key = normalize_option_name(name)
        if not key:
            return None
        hit = self._norm_index(list_type).get(key)
        return hit[0] if hit else None

    def canonical_name(self, list_type: str, name: str) -> str:
        """The LIST's spelling of ``name`` -- what a name-matching consumer
        (the BSG matrix RESTlet resolves options by exact name) must be sent.

        A punctuation variant comes back as the existing value's name
        (``'Khaki/ Coffee'`` -> ``'Khaki/Coffee'``); an exact or genuinely-new
        name comes back unchanged. Without this, the pre-pass correctly
        declined to create the variant and the RESTlet then rejected the child
        with "color 'Khaki/ Coffee' not in customlist_bsg_matrix_color"
        (live pilot retry, run 31036109523).
        """
        name = (name or "").strip()
        if not name:
            return name
        # Always answer from the normalized index, which prefers ACTIVE values.
        # Consulting the exact-match path first was wrong: when a name matches
        # a RETIRED value exactly, it came back unchanged and the RESTlet then
        # resolved it to that retired id, which NetSuite rejects outright.
        hit = self._norm_index(list_type).get(normalize_option_name(name))
        return hit[1] if hit else name

    def _norm_index(self, list_type: str) -> dict[str, tuple[str, str]]:
        """normalized name -> (internal id, list name) for a whole list.

        One query per list rather than one per miss: a create run resolves tens
        of thousands of SKUs, and the misses are exactly the rows that would
        otherwise each cost a round trip.
        """
        cached = self._norm_cache.get(list_type)
        if cached is not None:
            return cached
        rows = self.client.suiteql(
            f"SELECT id, name, isinactive FROM {list_type} ORDER BY id"
        )
        # Active values win, and among equals the lowest id -- the same rule
        # _find uses, so both paths land on the same canonical value. Rows
        # arrive id-ascending, so the first active hit per key is the winner.
        index: dict[str, tuple[str, str]] = {}
        have_active: set[str] = set()
        for row in rows:
            list_name = str(row.get("name") or "")
            norm = normalize_option_name(list_name)
            if not norm:
                continue
            entry = (str(row["id"]), list_name)
            active = str(row.get("isinactive") or "F") != "T"
            if norm not in index:
                index[norm] = entry
            elif active and norm not in have_active:
                index[norm] = entry  # promote over an earlier retired duplicate
            if active:
                have_active.add(norm)
        self._norm_cache[list_type] = index
        return index

    # convenience wrappers for the per-SKU (REST sync) path
    def color_id(self, color_name: str) -> str | None:
        return self.resolve(COLOR_LIST, color_name)[0]

    def size_id(self, raw_size: str) -> str | None:
        return self.resolve(SIZE_LIST, normalize_size(raw_size))[0]


@dataclass
class MatrixOptionReport:
    """Tally of what was found / created (or would be created) per dimension."""

    allow_create: bool
    existing: dict[str, list[str]] = field(
        default_factory=lambda: {"color": [], "size": []}
    )
    changed: dict[str, list[str]] = field(  # created, or "would create" in dry run
        default_factory=lambda: {"color": [], "size": []}
    )

    def add(self, kind: str, name: str, status: str) -> None:
        if status == "existing":
            self.existing[kind].append(name)
        elif status in ("created", "missing"):
            self.changed[kind].append(name)

    @property
    def created_count(self) -> int:
        return sum(len(v) for v in self.changed.values())

    def summary(self) -> str:
        verb = "created" if self.allow_create else "would create"
        lines = []
        for kind in ("color", "size"):
            present = len(self.existing[kind])
            new = sorted(self.changed[kind])
            line = f"{kind}s: {present} already present; {verb} {len(new)}"
            if new:
                line += f" ({', '.join(new)})"
            lines.append(line)
        if not self.allow_create and self.created_count:
            lines.append("(dry run — set SYNC_DRY_RUN=false to create these)")
        return "\n".join(lines)


def ensure_matrix_options(
    client: NetSuiteClient,
    styles: Iterable[StyleRecord],
    *,
    allow_create: bool,
) -> MatrixOptionReport:
    """Ensure every color/size used by ``styles`` exists in its matrix list."""
    colors, sizes = collect_options(styles)
    resolver = MatrixOptionResolver(client=client, allow_create=allow_create)
    report = MatrixOptionReport(allow_create=allow_create)
    for kind, list_type, names in (
        ("color", COLOR_LIST, colors),
        ("size", SIZE_LIST, sizes),
    ):
        for name in sorted(names):
            _id, status = resolver.resolve(list_type, name)
            report.add(kind, name, status)
    return report

"""Build matrix-child payloads for the BSG matrix RESTlet.

The RESTlet (``suitescript/bsg_sanmar_matrix.js``) creates matrix *children*
under existing parents — the one operation the CSV Import Assistant can't do for
numeric-style parents and the REST record API can't do at all. Each child is one
SanMar SKU; the payload mirrors the proven field choices from
:mod:`sanmar_netsuite.transform.csv_export`, reshaped into the RESTlet's JSON.

The RESTlet resolves the parent by *name* via SuiteScript search (which, unlike
the CSV importer, never coerces a numeric name into an internal id), so numeric
styles like ``2000`` and alphanumeric styles like ``K420`` use one code path.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from decimal import Decimal

from ..models import SkuRecord, StyleRecord
from ..netsuite.repository import child_external_id
from .csv_export import (
    DEFAULT_COSTING_METHOD,
    DEFAULT_DEPARTMENT,
    DEFAULT_LOCATION,
    DEFAULT_SUBSIDIARY,
    class_for_category,
)
from .sizes import normalize_size

DEFAULT_CURRENCY = "US Dollar"

# NetSuite's costingmethod field takes a code, not the display label the CSV uses.
_COSTING_CODE = {"average": "AVG", "lifo": "LIFO", "fifo": "FIFO", "standard": "STD"}


def _f(value: Decimal | int | None) -> float | None:
    return None if value is None else float(value)


def build_child_payload(
    sku: SkuRecord,
    style: StyleRecord,
    *,
    income_account: str,
    cogs_account: str,
    asset_account: str,
    tax_schedule: str,
) -> dict[str, object]:
    """Build the RESTlet item payload for a single SanMar SKU (matrix child)."""
    size = normalize_size(sku.size)
    return {
        "externalId": child_external_id(sku.unique_key),
        "itemId": f"{sku.style}-{sku.color_name}-{size}",
        "style": sku.style,  # parent matrix item Name/Number — resolved by name
        "color": sku.color_name,
        "size": size,
        "displayName": style.title[:60],
        "description": sku.description or style.description,
        "vendorName": sku.style,  # the vendor's code for the item (= the style)
        "incomeAccount": income_account,
        "cogsAccount": cogs_account,
        "assetAccount": asset_account,
        "taxSchedule": tax_schedule,
        "subsidiary": DEFAULT_SUBSIDIARY,
        "department": DEFAULT_DEPARTMENT,
        "class": class_for_category(style.category),
        "location": DEFAULT_LOCATION,
        "costingMethod": _COSTING_CODE.get(DEFAULT_COSTING_METHOD.lower(), "AVG"),
        "cost": _f(sku.case_price if sku.case_price is not None else sku.piece_price),
        "basePrice": _f(sku.msrp),
        "currency": DEFAULT_CURRENCY,
    }


def iter_child_payloads(
    styles: Iterable[StyleRecord],
    *,
    income_account: str,
    cogs_account: str,
    asset_account: str,
    tax_schedule: str,
    style_filter: str | None = None,
    limit: int = 0,
) -> Iterator[dict[str, object]]:
    """Yield child payloads across ``styles``.

    ``style_filter`` restricts to a single style name (case-insensitive); a
    positive ``limit`` caps the number of payloads (handy for a one-child smoke
    test). Yields in feed order so ``limit`` is deterministic.
    """
    wanted = style_filter.strip().casefold() if style_filter else None
    emitted = 0
    for style in styles:
        for sku in style.skus:
            if wanted is not None and sku.style.casefold() != wanted:
                continue
            yield build_child_payload(
                sku,
                style,
                income_account=income_account,
                cogs_account=cogs_account,
                asset_account=asset_account,
                tax_schedule=tax_schedule,
            )
            emitted += 1
            if limit and emitted >= limit:
                return

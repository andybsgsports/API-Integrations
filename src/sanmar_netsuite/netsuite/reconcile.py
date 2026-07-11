"""Reconcile field values NetSuite's CSV matrix import can't apply to children.

The Import Assistant nests matrix children but does not reliably set the income
account or base price on them (children inherit the parent's accounts, and the
price sublist is skipped). After the structural CSV import, this reconciles
those fields by external id over REST so every SanMar child matches the intended
values — income account and Base Price (= SanMar MSRP).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import StyleRecord
from .repository import ItemRepository, child_external_id

# The Base Price level. NetSuite's price sublist needs the full identifier set
# (currency page, price level, quantity) plus the amount.
_BASE_PRICE_LEVEL = "1"


@dataclass
class ReconcileReport:
    updated: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    skipped: int = 0

    def summary(self) -> str:
        parts = [f"reconciled {len(self.updated)} item(s)"]
        if self.missing:
            parts.append(f"{len(self.missing)} not found (import them first)")
        if self.skipped:
            parts.append(f"{self.skipped} unchanged/skipped")
        return "; ".join(parts)


def _reconcile_body(sku, income_account_id: str) -> dict:
    body: dict = {"incomeAccount": {"id": income_account_id}}
    if sku.msrp is not None:
        body["price"] = {
            "items": [
                {
                    "currencyPage": 1,
                    "priceLevel": {"id": _BASE_PRICE_LEVEL},
                    "quantity": {"value": 0},
                    "price": float(sku.msrp),
                }
            ]
        }
    return body


def reconcile_items(
    repo: ItemRepository,
    styles: list[StyleRecord],
    *,
    income_account_id: str,
    allow_write: bool,
) -> ReconcileReport:
    """Set income account + Base Price on every SanMar child that exists.

    ``income_account_id`` is the account's internal id. Children that aren't in
    NetSuite yet (not imported) are reported as missing, not created.
    """
    report = ReconcileReport()
    for style in styles:
        for sku in style.skus:
            ext = child_external_id(sku.unique_key)
            internal_id = repo.find_id_by_external_id(ext)
            if internal_id is None:
                report.missing.append(ext)
                continue
            if allow_write:
                repo.update_fields(internal_id, _reconcile_body(sku, income_account_id))
            report.updated.append(ext)
    return report

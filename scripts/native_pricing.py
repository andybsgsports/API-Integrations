"""Shared helpers for writing supplier pricing to NetSuite's NATIVE fields.

Base Price lives on the item's price sublist (same identifier set
reconcile.py established: currency page 1, price level 1, quantity 0);
Purchase Price is the item's ``cost`` field; shipping weight is ``weight``.
Current Base Prices are read from the ``pricing`` table for the diff --
tolerate that query failing by treating every price as unknown (the write
is idempotent anyway).
"""

from __future__ import annotations

BASE_PRICE_LEVEL = "1"


def base_price_body(price: float) -> dict:
    return {
        "items": [
            {
                "currencyPage": 1,
                "priceLevel": {"id": BASE_PRICE_LEVEL},
                "quantity": {"value": 0},
                "price": price,
            }
        ]
    }


def read_base_prices(client, id_in_list: str) -> dict[str, str]:
    """Item id -> current Base Price for the ids in an SQL ``IN`` list."""
    out: dict[str, str] = {}
    try:
        for r in client.suiteql(
            f"SELECT item, unitprice FROM pricing "
            f"WHERE pricelevel = {BASE_PRICE_LEVEL} AND item IN ({id_in_list})"
        ):
            out[str(r["item"])] = str(r.get("unitprice") or "")
    except Exception as exc:  # noqa: BLE001
        print(f"  (base-price read failed, writing unconditionally: {str(exc)[:80]})")
    return out


def add_native_diffs(
    body: dict,
    row: dict,
    base_by_rid: dict[str, str],
    rid: str,
    *,
    price: float | None,
    cost: float | None,
    weight: float | None,
    same,
) -> None:
    """Extend ``body`` with price/cost/weight wherever the target differs."""
    if price is not None and not same(base_by_rid.get(rid), price):
        body["price"] = base_price_body(price)
    if cost is not None and not same(row.get("cost"), cost):
        body["cost"] = cost
    if weight is not None and not same(row.get("weight"), weight):
        body["weight"] = weight

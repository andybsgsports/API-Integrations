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

# NetSuite weightUnit enum strings. "oz" is our best-informed guess (matches
# the item_uom_fix.py convention already used for "lb", which is confirmed
# live) -- verify against a real single-item write before a broad rollout,
# same as any other never-before-used enum value in this codebase.
WEIGHT_UNIT_OZ = "oz"
WEIGHT_UNIT_LB = "lb"
# Items lighter than this display in ounces instead of pounds (0.3 lb reads
# oddly small; 4.8 oz reads naturally) -- both SanMar and S&S report piece
# weight in pounds, so the number is converted to match whichever unit wins.
OZ_THRESHOLD_LB = 1.0


def weight_display(weight_lb: float | None) -> tuple[float | None, str | None]:
    """(display_weight, unit) for a weight expressed in pounds.

    weightUnit is a real physical-quantity label (NetSuite uses it for
    shipping calculations), not cosmetic -- so the NUMBER is converted to
    match whichever unit is chosen, never left as a bare pound value under
    an "oz" label. Returns ``(None, None)`` when weight_lb is None (nothing
    to base a choice on -- caller should leave both fields untouched).
    """
    if weight_lb is None:
        return None, None
    w = float(weight_lb)
    if w < OZ_THRESHOLD_LB:
        return round(w * 16, 2), WEIGHT_UNIT_OZ
    return w, WEIGHT_UNIT_LB


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
    weight_unit: str | None = None,
    same,
) -> None:
    """Extend ``body`` with price/cost/weight(+unit) wherever the target differs.

    weight and weight_unit are written together -- they must always agree
    (weightUnit is a real unit label, not cosmetic), so callers should
    compute both from the same source value (see weight_display)."""
    if price is not None and not same(base_by_rid.get(rid), price):
        body["price"] = base_price_body(price)
    if cost is not None and not same(row.get("cost"), cost):
        body["cost"] = cost
    if weight is not None and not same(row.get("weight"), weight):
        body["weight"] = weight
    if weight_unit and str(row.get("weightunit") or "") != weight_unit:
        body["weightUnit"] = weight_unit

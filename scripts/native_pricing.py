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

# weightUnit is a REFERENCE field, not a string. A REST GET returns
# {'id': '1', 'refName': 'lb'}, and a PATCH must send {'id': ...} to match --
# sending the bare string "lb" is rejected with "Invalid Field Value lb for the
# following field: weightunit", which (because NetSuite validates a record as a
# unit) failed all 45,531 items on the 2026-07-24 run.
#
# Ids confirmed against the account, not assumed: SuiteQL GROUP BY weightunit
# returns the internal id, and a REST GET of an item carrying each id gives its
# name. Verified by scripts/ns_weightunit_probe.py.
WEIGHT_UNIT_LB_ID = "1"
WEIGHT_UNIT_OZ_ID = "2"
WEIGHT_UNIT_OZ = {"id": WEIGHT_UNIT_OZ_ID}
WEIGHT_UNIT_LB = {"id": WEIGHT_UNIT_LB_ID}


def weight_display(
    weight_lb: float | None,
) -> tuple[float | None, dict[str, str] | None]:
    """(weight, unit reference) for a weight expressed in pounds.

    This is the SHIPPING weight of the finished item, and it is always kept
    in pounds -- one consistent unit across the whole catalogue (business
    decision 2026-07-29; an earlier version converted sub-1-lb items to
    ounces for readability, which just made tees look inconsistent next to
    jackets). Returns ``(None, None)`` when weight_lb is None (nothing to
    write -- caller should leave both fields untouched).

    The unit comes back as NetSuite's reference shape ``{"id": ...}``.
    """
    if weight_lb is None:
        return None, None
    return float(weight_lb), WEIGHT_UNIT_LB


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
    weight_unit: dict[str, str] | str | None = None,
    same,
) -> None:
    """Extend ``body`` with price/cost/weight(+unit) wherever the target differs.

    weight and weight_unit are written together -- they must always agree
    (weightUnit is a real unit label, not cosmetic), so callers should
    compute both from the same source value (see weight_display).

    weight_unit is NetSuite's reference shape ``{"id": ...}``; a bare string is
    accepted and treated as the id, since SuiteQL reports the current value as
    that id and the diff has to compare like with like."""
    if price is not None and not same(base_by_rid.get(rid), price):
        body["price"] = base_price_body(price)
    if cost is not None and not same(row.get("cost"), cost):
        body["cost"] = cost
    if weight is not None and not same(row.get("weight"), weight):
        body["weight"] = weight
    if weight_unit:
        want_id = weight_unit["id"] if isinstance(weight_unit, dict) else str(weight_unit)
        # SuiteQL returns the internal id ("1"/"2"), so compare ids, not names.
        if str(row.get("weightunit") or "") != want_id:
            body["weightUnit"] = {"id": want_id}

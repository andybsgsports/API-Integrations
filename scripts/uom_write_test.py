"""One-item experiment: find the PATCH shape that switches an item to Pair.

The bulk pair fix failed setting unitsType=6 + stock/purchase/sale=13 in one
PATCH ("Invalid value for purchaseUnit") -- likely because the new units are
validated against the item's still-current (Each) unitsType. This tries the
candidate shapes on ONE real bottom, in order, stopping at the first that
sticks, and prints the item's UOM before/after each. The guinea-pig is a
genuine bottom, so ending up as Pair needs no cleanup.

Env: ITEM_ID (default 30251 = Girls Pulse Team Shorts). Honors SYNC_DRY_RUN
(dry = just print current UOM, no writes).
"""

from __future__ import annotations

import os

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

PAIR_TYPE = "6"
PAIR_UNIT = "13"


def _uom(client, item_id: str) -> dict:
    rec = client.get_record("inventoryItem", item_id)
    out = {}
    for f in ("unitsType", "stockUnit", "purchaseUnit", "saleUnit"):
        v = rec.get(f)
        out[f] = v.get("id") if isinstance(v, dict) else v
    return out


def _try(client, item_id: str, label: str, body: dict) -> bool:
    print(f"\n-- attempt: {label}")
    print(f"   body: {body}")
    try:
        client.update_record("inventoryItem", item_id, body)
    except Exception as exc:  # noqa: BLE001
        print(f"   REJECTED: {str(exc)[:120]}")
        print(f"   FULL PAYLOAD: {getattr(exc, 'payload', '')}")
        return False
    print(f"   ACCEPTED; UOM now: {_uom(client, item_id)}")
    return True


def main() -> int:
    cfg = get_config()
    item_id = os.environ.get("ITEM_ID", "30251").strip()
    client = NetSuiteClient(cfg.netsuite)

    print(f"item {item_id} UOM before: {_uom(client, item_id)}")
    if cfg.sync.dry_run:
        print("dry run -- no writes")
        return 0

    # Candidate shapes, most-preferred first; stop at the first that sticks.
    if _try(client, item_id, "unitsType only (let units default to base)",
            {"unitsType": {"id": PAIR_TYPE}}):
        after = _uom(client, item_id)
        if after.get("stockUnit") == PAIR_UNIT or str(after.get("unitsType")) == PAIR_TYPE:
            print("\nRESULT: setting unitsType alone works.")
            return 0

    # Two-step: type first (already attempted above), then the units.
    if _try(client, item_id, "units only, after unitsType already Pair",
            {"stockUnit": {"id": PAIR_UNIT}, "purchaseUnit": {"id": PAIR_UNIT},
             "saleUnit": {"id": PAIR_UNIT}}):
        print("\nRESULT: two-step (unitsType, then units) works.")
        return 0

    # Last resort: everything in one body but with string ids (not {id:...}).
    if _try(client, item_id, "one body, bare string ids",
            {"unitsType": PAIR_TYPE, "stockUnit": PAIR_UNIT,
             "purchaseUnit": PAIR_UNIT, "saleUnit": PAIR_UNIT}):
        print("\nRESULT: bare-string-id single body works.")
        return 0

    print("\nRESULT: none of the candidate shapes worked -- see rejections above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

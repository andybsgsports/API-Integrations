"""Who may write an item's NATIVE price/cost/weight: the Preferred Vendor.

SanMar and S&S carry ~13.5k of the same physical products, and until
2026-07-31 each nightly writer computed Base Price / Purchase Price / weight
from ITS OWN feed and overwrote the other's numbers -- the same items flipped
between the two vendors' values every night (caught by the convergence
diagnostics in run 30627745124: price 31.90 <-> 34.86, cost 13.56 <-> 17.43
on item 132749; ~14k items re-priced nightly on EACH side, most of both jobs'
write volume and 429 pressure). Decision (Andy, 2026-07-31): the item's
**Preferred Vendor** decides whose numbers stick.

The Vendors sublist and its Preferred flag are maintained by
``scripts/vendor_sublist.py`` under the agreed ranking SanMar > Momentec >
S&S > UA, and can be re-pointed per item in NetSuite -- ownership follows the
flag wherever it points, so flipping an item's Preferred Vendor flips which
feed prices it on the next nightly. Items with no Preferred Vendor line fall
back to that same ranking among the feeds that actually match the item.

Only the native/shared fields are gated (Base Price, ``cost``, ``weight``,
``weightUnit``, the shared On Sale checkbox, and the ``custitem_feed_source``
identity stamp). The vendor-namespaced ``custitem_sanmar_*`` /
``custitem_ss_*`` fields and per-warehouse quantities never conflict and are
not gated.
"""

from __future__ import annotations

from vendor_sublist import KEY_FIELDS, RANKING

VENDOR_SANMAR = 512
VENDOR_MOMENTEC = 264
VENDOR_SS = 510
VENDOR_UA = 576


def read_preferred(client, id_in_list: str) -> dict[str, int]:
    """Item id -> Preferred Vendor id for the ids in an SQL ``IN`` list.

    Tolerates the query failing (sustained throttling) by returning an empty
    map -- callers then fall back to the ranking, which at worst repeats the
    old both-sides-write behaviour for one chunk and self-heals on the next
    diff-aware run.
    """
    out: dict[str, int] = {}
    try:
        for r in client.suiteql(
            "SELECT item, vendor FROM itemvendor "
            f"WHERE preferredvendor = 'T' AND item IN ({id_in_list})"
        ):
            out[str(r["item"])] = int(r["vendor"])
    except Exception as exc:  # noqa: BLE001
        print(f"  (preferred-vendor read failed, using ranking fallback: "
              f"{str(exc)[:80]})")
    return out


def owns_pricing(my_vid: int, preferred_vid: int | None, row: dict) -> bool:
    """Does MY feed own this item's native price/cost/weight?

    - Preferred Vendor set -> ownership follows it exactly (a vendor outside
      the feed ranking, e.g. one Andy points at manually, means NO feed owns
      pricing and the item keeps its manually managed numbers).
    - No Preferred Vendor -> vendor_sublist.py's ranking among the feeds that
      match this item: write unless a higher-ranked feed's key field is
      populated on the row.
    """
    if preferred_vid is not None:
        return preferred_vid == my_vid
    for vid in RANKING:
        if vid == my_vid:
            return True
        if str(row.get(KEY_FIELDS[vid]) or "").strip():
            return False
    return True

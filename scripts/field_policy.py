"""Which custom item fields the vendor writers read and write.

Andy, 2026-11-20: the pipeline keeps NetSuite's own fields current (cost and
the vendor line price, Base Price, weight, UPC, names and descriptions, the
item image, new styles / sizes / colours) plus the On Sale flag and Feed Last
Seen, for every vendor. The per-vendor reference fields are no longer written:
quantities (total and per warehouse), MAP / MSRP / case prices, status and
closeout flags, image links, colour and size names.

Quantities were the bulk of the nightly work: on 2026-11-20 the SanMar update
changed a quantity on about 40,000 items against 836 real price, cost or
weight updates.

What stays besides those two fields is each vendor's identifier, because other
steps use it to tell which feed an item belongs to (the Preferred Vendor
ranking, the image step, the description step). An identifier is written once
when an item is first matched and never changes, so it adds no nightly writes.

``VENDOR_CUSTOM_FIELDS=all`` restores every field.

A field that is kept but does not exist in the account (production has almost
none of them) is left out of every read and write, so a vendor step runs in
an account that only has the fields it needs.
"""

from __future__ import annotations

import os
import re

MODE = os.environ.get("VENDOR_CUSTOM_FIELDS", "minimal").strip().lower() or "minimal"

#: Kept by name.
KEEP = {
    "custitem_bsg_on_sale",          # cost is a temporary vendor sale price
    "custitem_feed_last_seen",       # when the item was last in its vendor's feed
    "custitem_feed_source",          # which vendor's feed that was (item lifecycle)
    "custitem_atlas_item_image",     # the item image (NetSuite's own Image field)
    "custitem_bsg_color",            # matrix options
    "custitem_bsg_size",
    # Vendor identifiers (see the module docstring).
    "custitem_sanmar_unique_key",
    "custitem_sanmar_style",
    "custitem_ss_sku",
    "custitem_ss_brand",             # S&S brand wins Manufacturer on shared items
    "custitem_mtec_item_sku",
}
#: DC OneSource vendors (Under Armour, Champro, ...): part id and style.
_KEEP_PATTERN = re.compile(r"^custitem_[a-z0-9]+_(part_id|style)$")

_exists: dict[str, bool] = {}


def is_custom(field: str) -> bool:
    return field.lower().startswith("custitem")


def keep(field: str) -> bool:
    """May the vendor writers read and write ``field``?"""
    f = (field or "").strip().lower()
    if not f or not is_custom(f) or MODE == "all":
        return True
    return f in KEEP or bool(_KEEP_PATTERN.match(f))


def exists(client, field: str) -> bool:
    """Is ``field`` a queryable item column in this account? (cached)"""
    f = field.strip().lower()
    if f not in _exists:
        try:
            client.suiteql(f"SELECT {f} FROM item WHERE rownum <= 1")
            _exists[f] = True
        except Exception:  # noqa: BLE001 - unknown column: not in this account
            _exists[f] = False
    return _exists[f]


def usable(client, fields) -> list[str]:
    """``fields`` that are kept AND exist here, in order, without repeats."""
    wanted: list[str] = []
    for field in fields:
        f = (field or "").strip()
        if f and f not in wanted and keep(f):
            wanted.append(f)
    custom = [f for f in wanted if is_custom(f)]
    if custom and not all(f.lower() in _exists for f in custom):
        try:  # one query when they all exist, the usual case in the sandbox
            client.suiteql(f"SELECT {', '.join(custom)} FROM item WHERE rownum <= 1")
            for f in custom:
                _exists[f.lower()] = True
        except Exception:  # noqa: BLE001 - at least one is missing: ask one by one
            pass
    return [f for f in wanted if not is_custom(f) or exists(client, f)]


def select(fields) -> str:
    """``", a, b"`` to append to a SELECT list ('' when there is nothing)."""
    return "".join(f", {f}" for f in fields)


def clean(body: dict, allowed) -> dict:
    """``body`` without custom fields outside ``allowed`` (the usable set).

    Native fields always pass. Called on every record body just before it is
    counted and written, so a field that is dropped or missing can neither be
    written nor make an item look changed.
    """
    ok = {f.lower() for f in allowed}
    return {k: v for k, v in body.items() if not is_custom(k) or k.lower() in ok}


def report(label: str, requested, used) -> None:
    left = sorted({f for f in requested if f and is_custom(f)} - set(used))
    if left:
        dropped = [f for f in left if not keep(f)]
        missing = [f for f in left if keep(f)]
        if dropped:
            print(f"NOTE: {label}: {len(dropped)} vendor reference field(s) are no "
                  f"longer written (VENDOR_CUSTOM_FIELDS={MODE}).")
        if missing:
            print(f"NOTE: {label}: field(s) not in this account, skipped: "
                  f"{', '.join(missing)}")

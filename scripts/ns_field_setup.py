"""Audit (and optionally create) the custom item fields the syncs write.

For every field in the SanMar and S&S field maps: check whether it exists
(SuiteQL probe), and when ``FIELD_CREATE=true`` attempt to create missing ones
via the REST record API (``itemcustomfield``). NetSuite's REST support for
field *definitions* is not officially documented, so each attempt is reported
individually — anything the API refuses lands on a click-list for manual
creation in the UI.
"""

from __future__ import annotations

import os

from warehouse_fields import SANMAR_WHSE_FIELDS, SS_WHSE_FIELDS

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

# (scriptid, label, fieldtype, notes) — fieldtype per NetSuite conventions.
FIELDS: list[tuple[str, str, str, str]] = [
    # SanMar
    ("custitem_sanmar_unique_key", "SanMar Unique Key", "TEXT", ""),
    ("custitem_sanmar_inventory_key", "SanMar Inventory Key", "TEXT", ""),
    ("custitem_sanmar_size_index", "SanMar Size Index", "TEXT", ""),
    ("custitem_sanmar_style", "SanMar Style", "TEXT", ""),
    ("custitem_sanmar_mf_color", "SanMar Mainframe Color", "TEXT", ""),
    ("custitem_sanmar_gtin", "SanMar GTIN", "TEXT", ""),
    ("custitem_sanmar_map", "SanMar MAP Price", "CURRENCY", ""),
    ("custitem_sanmar_msrp", "SanMar MSRP", "CURRENCY", ""),
    ("custitem_sanmar_case_price", "SanMar Case Price", "CURRENCY", ""),
    ("custitem_sanmar_case_size", "SanMar Case Size", "INTEGER", ""),
    ("custitem_sanmar_status", "SanMar Product Status", "TEXT", ""),
    ("custitem_sanmar_qty_available", "SanMar Qty Available", "INTEGER", ""),
    ("custitem_sanmar_front_image_url", "SanMar Front Image URL", "URL", ""),
    # S&S Activewear
    ("custitem_ss_sku", "S&S SKU", "TEXT", ""),
    ("custitem_ss_style_id", "S&S Style ID", "TEXT", ""),
    ("custitem_ss_style", "S&S Style Name", "TEXT", ""),
    ("custitem_ss_color_name", "S&S Color Name", "TEXT", ""),
    ("custitem_ss_color_code", "S&S Color Code", "TEXT", ""),
    ("custitem_ss_size_name", "S&S Size Name", "TEXT", ""),
    ("custitem_ss_size_order", "S&S Size Order", "TEXT", ""),
    ("custitem_ss_gtin", "S&S GTIN", "TEXT", ""),
    ("custitem_ss_brand", "S&S Brand", "TEXT", ""),
    ("custitem_ss_map", "S&S MAP Price", "CURRENCY", ""),
    ("custitem_ss_msrp", "S&S MSRP", "CURRENCY", ""),
    ("custitem_ss_piece_price", "S&S Piece Price", "CURRENCY", ""),
    ("custitem_ss_dozen_price", "S&S Dozen Price", "CURRENCY", ""),
    ("custitem_ss_case_price", "S&S Case Price", "CURRENCY", ""),
    ("custitem_ss_case_size", "S&S Case Size", "INTEGER", ""),
    ("custitem_ss_weight", "S&S Weight", "FLOAT", ""),
    ("custitem_ss_qty_available", "S&S Qty Available", "INTEGER", ""),
    ("custitem_ss_is_closeout", "S&S Is Closeout", "CHECKBOX", ""),
    ("custitem_ss_is_discontinued", "S&S Is Discontinued", "CHECKBOX", ""),
    ("custitem_ss_front_image_url", "S&S Front Image URL", "URL", ""),
    ("custitem_ss_on_model_image_url", "S&S On-Model Image URL", "URL", ""),
    # Momentec (for the upcoming sync)
    ("custitem_mtec_item_sku", "Momentec Item SKU", "TEXT", ""),
    ("custitem_mtec_style", "Momentec Style", "TEXT", ""),
    ("custitem_mtec_gtin", "Momentec GTIN", "TEXT", ""),
    ("custitem_mtec_msrp", "Momentec MSRP", "CURRENCY", ""),
    ("custitem_mtec_cost", "Momentec Cost", "CURRENCY", ""),
    ("custitem_mtec_case_size", "Momentec Case Pack Qty", "INTEGER", ""),
    ("custitem_mtec_qty_available", "Momentec Qty Available", "INTEGER", ""),
    # Momentec Qty By Warehouse intentionally absent: they ship from a single
    # warehouse, so Qty Available IS the per-warehouse number (user request).
    ("custitem_mtec_front_image_url", "Momentec Back Image URL", "URL", ""),
    ("custitem_mtec_size_guide", "Momentec Size Guide", "URL", ""),
    ("custitem_mtec_instock_guaranteed", "Momentec In-Stock Guaranteed", "CHECKBOX", ""),
    # Under Armour (DC OneSource)
    ("custitem_ua_part_id", "UA Part ID", "TEXT", ""),
    ("custitem_ua_style", "UA Style", "TEXT", ""),
    ("custitem_ua_gtin", "UA GTIN", "TEXT", ""),
    # UA MSRP/Cost intentionally absent: pricing lives on the native fields
    # (Base Price = UA list price, Purchase Price/cost = UA net cost).
    ("custitem_ua_qty_available", "UA Qty Available", "INTEGER", ""),
    # UA Qty By Warehouse intentionally absent: DC OneSource reports a single
    # fulfillment location, so Qty Available IS the per-warehouse number.
    ("custitem_ua_front_image_url", "UA Front Image URL", "URL", ""),
]

# Per-warehouse quantity columns (user-requested): one INTEGER field per
# SanMar / S&S warehouse, from the shared maps in warehouse_fields.py.
FIELDS += [
    (sid, label, "INTEGER", "")
    for sid, label in list(SANMAR_WHSE_FIELDS.values()) + list(SS_WHSE_FIELDS.values())
]

# DC OneSource supplier expansion (see scripts/dcos_backfill.py): same key
# set as UA, no pricing fields (price lists are reference-only for now).
for _prefix, _label in (
    ("champro", "Champro"),
    ("usb", "USB"),
    ("tck", "TCK"),
    ("capamerica", "Cap America"),
    ("mizuno", "Mizuno"),
    ("ripit", "Rip-It"),
    ("baden", "Baden"),
):
    FIELDS += [
        (f"custitem_{_prefix}_part_id", f"{_label} Part ID", "TEXT", ""),
        (f"custitem_{_prefix}_style", f"{_label} Style", "TEXT", ""),
        (f"custitem_{_prefix}_gtin", f"{_label} GTIN", "TEXT", ""),
        (f"custitem_{_prefix}_qty_available", f"{_label} Qty Available", "INTEGER", ""),
    ]

# Champro reference-doc links: merged sizing guide + fabrics PDFs uploaded to
# the File Cabinet (scripts/champro_docs_upload.py) and set on every Champro
# item by dcos_backfill.
FIELDS += [
    ("custitem_champro_size_guide", "Champro Size Guide", "URL", ""),
    ("custitem_champro_fabrics", "Champro Fabrics", "URL", ""),
]

# Item lifecycle (auto-inactivate discontinued / auto-create new): every feed
# writer stamps these on the items it matches, and item_lifecycle.py
# inactivates items whose stamp goes stale past the grace period.
FIELDS += [
    ("custitem_feed_source", "Feed Source", "TEXT", ""),
    ("custitem_feed_last_seen", "Feed Last Seen", "DATE", ""),
]

# On-sale flag: the field updates check this when an item's current cost is a
# vendor sale price below its regular price (SanMar dip sale, S&S sale price).
FIELDS += [
    ("custitem_bsg_on_sale", "On Sale", "CHECKBOX", ""),
]


def field_exists(client: NetSuiteClient, scriptid: str) -> bool:
    try:
        client.suiteql(f"SELECT {scriptid} FROM item WHERE rownum <= 1")
        return True
    except Exception:  # noqa: BLE001 - unknown column -> missing
        return False


def main() -> int:
    allow_create = (os.environ.get("FIELD_CREATE") or "").lower() == "true"
    client = NetSuiteClient(get_config().netsuite)

    existing, missing = [], []
    for sid, label, ftype, _ in FIELDS:
        (existing if field_exists(client, sid) else missing).append((sid, label, ftype))
    print(f"existing: {len(existing)}  missing: {len(missing)}")
    for sid, _label, _ in existing:
        print(f"  HAVE  {sid}")

    created, failed = [], []
    for sid, label, ftype in missing:
        if not allow_create:
            print(f"  NEED  {sid}  ({label}, {ftype})")
            continue
        body = {
            "label": label,
            "scriptid": sid,
            "fieldtype": ftype,
            "appliestoinventory": True,
            "storevalue": True,
        }
        try:
            new_id = client.create_record("itemcustomfield", body)
            created.append(sid)
            print(f"  CREATED  {sid} -> id {new_id}")
        except Exception as exc:  # noqa: BLE001
            failed.append((sid, label, ftype, str(exc)[:160]))
            print(f"  FAILED   {sid}: {str(exc)[:160]}")

    if failed:
        print("\nManual click-list (Customization > Lists, Records & Fields > "
              "Item Fields > New; check 'Inventory Item' under Applies To):")
        for sid, label, ftype, _ in failed:
            print(f"  Label: {label:<28} ID: {sid:<36} Type: {ftype}")
    print(f"\nsummary: have={len(existing)} created={len(created)} "
          f"still-missing={len(missing) - len(created)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

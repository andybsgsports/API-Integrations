"""Human-readable descriptions for every custitem_* field this project
writes, so a random employee looking at an item record's Field Help popup
understands what the field means -- not just "custom field for your account,
contact your administrator."

Keyed by scriptid; must cover (at least) every field in
``scripts/ns_field_setup.py``'s ``FIELDS`` list.
"""

from __future__ import annotations

from warehouse_fields import SANMAR_WHSE_FIELDS, SS_WHSE_FIELDS

DESCRIPTIONS: dict[str, str] = {
    # SanMar
    "custitem_sanmar_unique_key": (
        "SanMar's unique key for this exact size/color combination. Used to "
        "match this item to SanMar's live inventory and pricing feed."
    ),
    "custitem_sanmar_inventory_key": (
        "SanMar's key for looking up this item's live warehouse inventory levels."
    ),
    "custitem_sanmar_size_index": (
        "SanMar's numeric sort order for this size, so sizes list correctly "
        "(S, M, L, XL...) instead of alphabetically."
    ),
    "custitem_sanmar_style": (
        "SanMar's style number for this product (shared by every color/size "
        "of the same garment)."
    ),
    "custitem_sanmar_mf_color": (
        "SanMar's internal 'mainframe' color code for this item, which can "
        "differ slightly from the color name shown to customers."
    ),
    "custitem_sanmar_gtin": "This item's barcode (GTIN/UPC) as provided by SanMar's product feed.",
    "custitem_sanmar_map": (
        "SanMar's Minimum Advertised Price for this item -- the lowest price "
        "we're allowed to advertise it at."
    ),
    "custitem_sanmar_msrp": "SanMar's Manufacturer's Suggested Retail Price for this item.",
    "custitem_sanmar_case_price": "SanMar's price per full case (bulk carton) of this item.",
    "custitem_sanmar_case_size": "How many units come in one case/carton from SanMar.",
    "custitem_sanmar_status": (
        "SanMar's current status for this item (e.g. Active, Discontinued, Closeout)."
    ),
    "custitem_sanmar_qty_available": (
        "Total quantity of this item currently available across all SanMar "
        "warehouses (updated nightly). Note: SanMar's inventory file caps each "
        "warehouse at 1,500, so this total can understate actual availability "
        "-- true on-hand may be higher."
    ),
    "custitem_sanmar_front_image_url": (
        "Despite the field name, this holds a link to this item's BACK-view "
        "product photo, hosted on SanMar's site. (The front-view photo is "
        "shown directly on this record's Item Image field instead.)"
    ),
    # S&S Activewear
    "custitem_ss_sku": (
        "S&S Activewear's unique SKU for this exact size/color combination -- "
        "used to match this item to their live catalog and inventory feed."
    ),
    "custitem_ss_style_id": "S&S Activewear's internal ID for this product's style/garment.",
    "custitem_ss_style": "S&S Activewear's style name for this product line.",
    "custitem_ss_color_name": "The color name as shown by S&S Activewear.",
    "custitem_ss_color_code": "S&S Activewear's internal color code.",
    "custitem_ss_size_name": "The size label as shown by S&S Activewear (e.g. M, L, XL).",
    "custitem_ss_size_order": (
        "S&S Activewear's numeric sort order for this size, so sizes list "
        "correctly instead of alphabetically."
    ),
    "custitem_ss_gtin": "This item's barcode (GTIN/UPC) as provided by S&S Activewear.",
    "custitem_ss_brand": "The brand name as shown by S&S Activewear.",
    "custitem_ss_map": "S&S Activewear's Minimum Advertised Price for this item.",
    "custitem_ss_msrp": "S&S Activewear's Manufacturer's Suggested Retail Price for this item.",
    "custitem_ss_piece_price": "S&S Activewear's price for a single unit.",
    "custitem_ss_dozen_price": "S&S Activewear's price per dozen units.",
    "custitem_ss_case_price": "S&S Activewear's price per full case of this item.",
    "custitem_ss_case_size": "How many units come in one case from S&S Activewear.",
    "custitem_ss_weight": "This item's shipping weight as provided by S&S Activewear.",
    "custitem_ss_qty_available": (
        "Total quantity of this item currently available from S&S Activewear "
        "(updated nightly). Note: S&S caps reported inventory at 500 per "
        "location, so this total can understate actual availability -- true "
        "on-hand may be higher until S&S enables full inventory visibility."
    ),
    "custitem_ss_is_closeout": (
        "Checked if S&S Activewear has marked this item as a closeout "
        "(limited remaining stock, being phased out)."
    ),
    "custitem_ss_is_discontinued": "Checked if S&S Activewear has discontinued this item.",
    "custitem_ss_front_image_url": (
        "Link to this item's front-view product photo, hosted on S&S Activewear's site."
    ),
    "custitem_ss_on_model_image_url": (
        "Link to a photo of this item being worn by a model, hosted on S&S "
        "Activewear's site."
    ),
    # Momentec Brands
    "custitem_mtec_item_sku": (
        "Momentec Brands' unique SKU for this exact size/color combination -- "
        "used to match this item to their catalog and inventory feed."
    ),
    "custitem_mtec_style": (
        "Momentec Brands' parent style number for this product line (shared "
        "by all colors/sizes)."
    ),
    "custitem_mtec_gtin": "This item's barcode (GTIN/UPC) as provided by Momentec Brands.",
    "custitem_mtec_msrp": "Momentec Brands' Manufacturer's Suggested Retail Price for this item.",
    "custitem_mtec_cost": "Momentec Brands' cost price for this item.",
    "custitem_mtec_case_size": "How many units come in one case pack from Momentec Brands.",
    "custitem_mtec_qty_available": (
        "Total quantity of this item currently available from Momentec Brands "
        "(updated nightly)."
    ),
    "custitem_mtec_front_image_url": (
        "Link to this item's product photo, hosted on Momentec Brands' site."
    ),
    "custitem_mtec_size_guide": (
        "Link to the size / fit guide for this item, from Momentec's product "
        "feed. Currently a single shared sizing chart across brands; will "
        "reflect a brand-specific guide automatically if the feed provides one."
    ),
    # Under Armour (DC OneSource / PromoStandards)
    "custitem_ua_part_id": (
        "Under Armour's part ID for this exact size/color combination, from "
        "the DC OneSource/PromoStandards feed."
    ),
    "custitem_ua_style": "Under Armour's style number for this product line.",
    "custitem_ua_gtin": "This item's barcode (GTIN/UPC) as provided by Under Armour.",
    "custitem_ua_qty_available": (
        "Total quantity of this item currently available from Under Armour "
        "(updated nightly)."
    ),
    "custitem_ua_front_image_url": (
        "Link to this item's product photo from Under Armour's DC OneSource catalog."
    ),
}

# DC OneSource expansion suppliers (same key set as UA, no pricing fields).
for _pfx, _name in (
    ("champro", "Champro"),
    ("usb", "United Sports Brands"),
    ("tck", "Twin City (TCK)"),
    ("capamerica", "Cap America"),
    ("mizuno", "Mizuno"),
    ("ripit", "Rip-It"),
    ("baden", "Baden"),
):
    DESCRIPTIONS[f"custitem_{_pfx}_part_id"] = (
        f"{_name}'s part ID for this exact size/color combination, from "
        "their DC OneSource/PromoStandards feed."
    )
    DESCRIPTIONS[f"custitem_{_pfx}_style"] = f"{_name}'s style number for this product line."
    DESCRIPTIONS[f"custitem_{_pfx}_gtin"] = (
        f"This item's barcode (GTIN/UPC) as provided by {_name}."
    )
    DESCRIPTIONS[f"custitem_{_pfx}_qty_available"] = (
        f"Total quantity of this item currently available from {_name} "
        "(updated nightly)."
    )

# Item lifecycle heartbeat (stamped by every feed writer, consumed by
# item_lifecycle.py to auto-inactivate discontinued items).
DESCRIPTIONS["custitem_feed_source"] = (
    "Which supplier feed maintains this item (e.g. sanmar, ss, momentec, ua, "
    "champro). Stamped automatically by the nightly syncs."
)
DESCRIPTIONS["custitem_feed_last_seen"] = (
    "The last date this item appeared in its supplier's feed. When this goes "
    "stale past the grace period, the item is automatically made inactive "
    "(discontinued)."
)

# Per-warehouse quantity columns (one INTEGER field per supplier warehouse).
for _no, (_sid, _label) in SANMAR_WHSE_FIELDS.items():
    _city = _label.removeprefix("SanMar Qty: ")
    DESCRIPTIONS[_sid] = (
        f"Quantity of this item currently available at SanMar's {_city} "
        f"warehouse (warehouse #{_no}, updated nightly). Note: SanMar's "
        f"inventory file caps this at 1,500 -- a value of 1,500 can mean "
        f"1,500 or more (actual on-hand may be higher)."
    )
for _abbr, (_sid, _label) in SS_WHSE_FIELDS.items():
    _city = _label.removeprefix("S&S Qty: ")
    DESCRIPTIONS[_sid] = (
        f"Quantity of this item currently available at S&S Activewear's "
        f"{_city} warehouse (code '{_abbr}', updated nightly). Note: S&S caps "
        f"API inventory at 500 per location -- a value of 500 can mean 500 or "
        f"more (actual on-hand may be higher)."
    )

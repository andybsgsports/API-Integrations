"""SanMar file names, warehouse mapping, and product-status constants.

Field layouts are documented in the *SanMar FTP Integration Guide v23.3*. The
parsers in :mod:`sanmar_netsuite.sanmar.parsers` read by column name (for the
CSV files) or by documented column index (for the pipe-delimited files), so the
canonical schema lives here.
"""

from __future__ import annotations

# ── Daily product files (in the SanMarPDD remote folder) ─────────────────────
# Primary catalog + pricing + image URLs, comma-delimited & quote-encapsulated.
FILE_SDL_N = "SanMar_SDL_N.csv"
# Catalog + bulk (all-warehouse) inventory, comma-delimited & quote-encapsulated.
FILE_EPDD = "SanMar_EPDD.csv"
# Inventory by warehouse + sale pricing, pipe-delimited, refreshed hourly.
FILE_DIP = "sanmar_dip.txt"
# Optional: your account's lowest available price, comma-delimited (by request).
FILE_DAILY_PRICING = "sanmar_dp.csv"

# ── SanMar warehouse number → human label ────────────────────────────────────
# Source: sanmar_dip.txt / activeproductsexport column F definition.
WAREHOUSES: dict[str, str] = {
    "1": "Seattle, WA",
    "2": "Cincinnati, OH",
    "3": "Dallas, TX",
    "4": "Reno, NV",
    "5": "Robbinsville, NJ",
    "6": "Jacksonville, FL",
    "7": "Minneapolis, MN",
    "12": "Phoenix, AZ",
    "31": "Richmond, VA",
}

# ── Product status values (PRODUCT_STATUS column) ────────────────────────────
STATUS_COMING_SOON = "Coming Soon"
STATUS_NEW = "New"
STATUS_REGULAR = "Regular"
STATUS_ACTIVE = "Active"
STATUS_DISCONTINUED = "Discontinued"

# Statuses we treat as "sellable / keep active in NetSuite".
ACTIVE_STATUSES = frozenset(
    {STATUS_NEW, STATUS_REGULAR, STATUS_ACTIVE}
)

# ── sanmar_dip.txt column indices (0-based), pipe-delimited, no header ───────
# Per FTP Integration Guide, "Daily Inventory & Sales Pricing Files".
DIP_INVENTORY_KEY = 0
DIP_SIZE_INDEX = 1
DIP_CATALOG_NO = 2
DIP_CATALOG_COLOR = 3
DIP_SIZE = 4
DIP_WHSE_NO = 5
DIP_QUANTITY = 6
DIP_PIECE_WEIGHT = 7
DIP_PIECE_PRICE = 8
DIP_DOZENS_PRICE = 9
DIP_CASE_PRICE = 10
DIP_CASE_SIZE = 11
DIP_EACH_SALE_PRICE = 12
DIP_DOZENS_SALE_PRICE = 13
DIP_CASE_SALE_PRICE = 14
DIP_SALE_START_DATETIME = 15
DIP_SALE_END_DATETIME = 16
DIP_UNIQUE_KEY = 17
DIP_DISCONTINUED_CODE = 18
DIP_MIN_COLUMNS = 19  # rows must have at least this many fields to be valid

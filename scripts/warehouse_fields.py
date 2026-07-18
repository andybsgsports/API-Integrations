"""Shared per-warehouse field maps for SanMar and S&S (user-requested).

One INTEGER custom field per supplier warehouse so availability shows as
real columns instead of a text blob. The existing ``*_qty_by_whse`` text
fields stay (readable summary + safety net for codes not in these maps).

SanMar's set mirrors the fixed table in ``sanmar.constants.WAREHOUSES``.
S&S publishes no such table and its DC network is mid-consolidation in
2026, so that set is the empirical superset observed via the per-SKU
``/Inventory/{sku}`` endpoint (the only S&S endpoint that carries a
per-warehouse breakdown; the filtered ``/Products`` batch endpoint never
returns one). A feed code missing from these maps is logged by the
writers, never silently dropped.
"""

from __future__ import annotations

# warehouse_no -> (scriptid, label); order mirrors sanmar.constants.WAREHOUSES.
SANMAR_WHSE_FIELDS: dict[str, tuple[str, str]] = {
    "1": ("custitem_sanmar_qty_seattle", "SanMar Qty: Seattle, WA"),
    "2": ("custitem_sanmar_qty_cincinnati", "SanMar Qty: Cincinnati, OH"),
    "3": ("custitem_sanmar_qty_dallas", "SanMar Qty: Dallas, TX"),
    "4": ("custitem_sanmar_qty_reno", "SanMar Qty: Reno, NV"),
    "5": ("custitem_sanmar_qty_robbinsville", "SanMar Qty: Robbinsville, NJ"),
    "6": ("custitem_sanmar_qty_jacksonville", "SanMar Qty: Jacksonville, FL"),
    "7": ("custitem_sanmar_qty_minneapolis", "SanMar Qty: Minneapolis, MN"),
    "12": ("custitem_sanmar_qty_phoenix", "SanMar Qty: Phoenix, AZ"),
    "31": ("custitem_sanmar_qty_richmond", "SanMar Qty: Richmond, VA"),
}

# warehouseAbbr -> (scriptid, label). Codes are as S&S's /Inventory API
# reports them; the city/state each maps to was confirmed with the user
# (S&S publishes no code->name table and its API returns only the abbr).
SS_WHSE_FIELDS: dict[str, tuple[str, str]] = {
    "IL": ("custitem_ss_qty_il", "S&S Qty: Lockport, IL"),
    "KS": ("custitem_ss_qty_ks", "S&S Qty: Olathe, KS"),
    "GA": ("custitem_ss_qty_ga", "S&S Qty: McDonough, GA"),
    "TX": ("custitem_ss_qty_tx", "S&S Qty: Fort Worth, TX"),
    "NV": ("custitem_ss_qty_nv", "S&S Qty: Reno, NV"),
    "OH": ("custitem_ss_qty_oh", "S&S Qty: West Chester, OH"),
    "PA": ("custitem_ss_qty_pa", "S&S Qty: Reading, PA"),
    "CN": ("custitem_ss_qty_cn", "S&S Qty: Fresno, CA"),
    "FO": ("custitem_ss_qty_fo", "S&S Qty: Orlando, FL"),
    "MA": ("custitem_ss_qty_ma", "S&S Qty: Middleboro, MA"),
    "DS": ("custitem_ss_qty_ds", "S&S Qty: Drop Ship"),
    "CC": ("custitem_ss_qty_cc", "S&S Qty: Bolingbrook, IL"),
}

SANMAR_QTY_FIELDS: list[str] = [sid for sid, _ in SANMAR_WHSE_FIELDS.values()]
SS_QTY_FIELDS: list[str] = [sid for sid, _ in SS_WHSE_FIELDS.values()]

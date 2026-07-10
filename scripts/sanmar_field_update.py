"""Nightly SanMar field update onto ADOPTED items, joined by UPC (runs on CI).

Fills the ``custitem_sanmar_*`` fields on every existing item whose upcCode
matches a feed GTIN — availability (total + per-warehouse), pricing
(MAP/MSRP/case), status, and the SanMar keys. Diff-aware: current values are
bulk-read first and only changed fields are written, so steady-state nightly
runs are small. Honors ``SYNC_DRY_RUN``; ``UPDATE_MAX_ITEMS`` caps writes.
"""

from __future__ import annotations

import os
from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_inventory, parse_styles
from sanmar_netsuite.sanmar.sftp_client import SanMarSftp

# scriptid -> SuiteQL column is the same token for custom fields.
FIELD_ORDER = [
    "custitem_sanmar_unique_key",
    "custitem_sanmar_inventory_key",
    "custitem_sanmar_size_index",
    "custitem_sanmar_style",
    "custitem_sanmar_mf_color",
    "custitem_sanmar_gtin",
    "custitem_sanmar_map",
    "custitem_sanmar_msrp",
    "custitem_sanmar_case_price",
    "custitem_sanmar_case_size",
    "custitem_sanmar_status",
    "custitem_sanmar_qty_available",
    "custitem_sanmar_qty_by_whse",
]


def _dl(cfg, name: str) -> Path:
    path = Path(cfg.sftp.download_dir) / name
    return path if path.exists() else SanMarSftp(cfg.sftp).download(name)


def _s(v) -> str:
    return "" if v is None else str(v)


def build_payloads(styles, inventory) -> dict[str, dict[str, str]]:
    """GTIN -> field payload for every feed SKU carrying a barcode."""
    whse_by_key: dict[str, str] = {}
    total_by_key: dict[str, int] = {}
    for rec in inventory:
        whse_by_key[rec.unique_key] = "; ".join(
            f"{w.warehouse_label or w.warehouse_no}: {w.quantity}"
            for w in rec.warehouses
        )
        total_by_key[rec.unique_key] = sum(w.quantity for w in rec.warehouses)

    payloads: dict[str, dict[str, str]] = {}
    for style in styles:
        for sku in style.skus:
            if not sku.gtin:
                continue
            qty = total_by_key.get(sku.unique_key, sku.available_qty)
            payloads[sku.gtin] = {
                "custitem_sanmar_unique_key": sku.unique_key,
                "custitem_sanmar_inventory_key": sku.inventory_key,
                "custitem_sanmar_size_index": sku.size_index,
                "custitem_sanmar_style": sku.style,
                "custitem_sanmar_mf_color": sku.mainframe_color,
                "custitem_sanmar_gtin": sku.gtin,
                "custitem_sanmar_map": _s(sku.map_price),
                "custitem_sanmar_msrp": _s(sku.msrp),
                "custitem_sanmar_case_price": _s(sku.case_price),
                "custitem_sanmar_case_size": _s(sku.case_size),
                "custitem_sanmar_status": sku.product_status,
                "custitem_sanmar_qty_available": _s(qty),
                "custitem_sanmar_qty_by_whse": whse_by_key.get(sku.unique_key, ""),
            }
    return payloads


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")

    styles = parse_styles(_dl(cfg, C.FILE_SDL_N))
    inventory = parse_inventory(_dl(cfg, C.FILE_DIP))
    payloads = build_payloads(styles, inventory)
    print(f"feed SKUs with GTIN: {len(payloads):,}")

    client = NetSuiteClient(cfg.netsuite)
    cols = ", ".join(FIELD_ORDER)
    gtins = sorted(payloads)
    considered = written = unchanged = failures = 0
    for i in range(0, len(gtins), 250):
        chunk = gtins[i : i + 250]
        in_list = ", ".join(f"'{_sql_escape(g)}'" for g in chunk)
        rows = client.suiteql(
            f"SELECT id, upccode, {cols} FROM item WHERE upccode IN ({in_list})"
        )
        for row in rows:
            gtin = str(row.get("upccode") or "")
            want = payloads.get(gtin)
            if not want:
                continue
            body = {
                f: v for f, v in want.items()
                if _s(row.get(f)).strip() != _s(v).strip()
            }
            if not body:
                unchanged += 1
                continue
            if max_items and considered >= max_items:
                continue
            considered += 1
            if not allow_write:
                written += 1
                continue
            try:
                client.update_record("inventoryItem", str(row["id"]), body)
                written += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if failures <= 10:
                    print(f"  FAILED item {row['id']}: {str(exc)[:150]}")

    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(f"\nsanmar field update: {verb} {written} item(s); "
          f"unchanged: {unchanged}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

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


def _make_put(entry: dict[str, object]):
    def put(field: str, value: object) -> None:
        if value is None or str(value).strip() == "":
            return
        entry[field] = value
    return put


def build_payloads(styles, inventory) -> dict[str, dict[str, object]]:
    """GTIN -> field payload for every feed SKU carrying a barcode.

    Values are typed (numbers as numbers) and empty values are omitted —
    NetSuite 400s on an empty string in a numeric/currency field.
    """
    whse_by_key: dict[str, str] = {}
    total_by_key: dict[str, int] = {}
    for rec in inventory:
        whse_by_key[rec.unique_key] = "; ".join(
            f"{w.warehouse_label or w.warehouse_no}: {w.quantity}"
            for w in rec.warehouses
        )
        total_by_key[rec.unique_key] = sum(w.quantity for w in rec.warehouses)

    payloads: dict[str, dict[str, object]] = {}
    for style in styles:
        for sku in style.skus:
            if not sku.gtin:
                continue
            entry: dict[str, object] = {}
            put = _make_put(entry)
            qty = total_by_key.get(sku.unique_key, sku.available_qty)
            put("custitem_sanmar_unique_key", sku.unique_key)
            put("custitem_sanmar_inventory_key", sku.inventory_key)
            put("custitem_sanmar_size_index", sku.size_index)
            put("custitem_sanmar_style", sku.style)
            put("custitem_sanmar_mf_color", sku.mainframe_color)
            put("custitem_sanmar_gtin", sku.gtin)
            put("custitem_sanmar_map", None if sku.map_price is None else float(sku.map_price))
            put("custitem_sanmar_msrp", None if sku.msrp is None else float(sku.msrp))
            put(
                "custitem_sanmar_case_price",
                None if sku.case_price is None else float(sku.case_price),
            )
            put("custitem_sanmar_case_size", None if sku.case_size is None else int(sku.case_size))
            put("custitem_sanmar_status", sku.product_status)
            put("custitem_sanmar_qty_available", None if qty is None else int(qty))
            put("custitem_sanmar_qty_by_whse", whse_by_key.get(sku.unique_key, ""))
            if entry:
                payloads[sku.gtin] = entry
    return payloads


def _same(current: object, new: object) -> bool:
    cs, ns_ = _s(current).strip(), str(new).strip()
    if cs == ns_:
        return True
    try:
        return float(cs) == float(ns_)
    except ValueError:
        return False


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
                f: v for f, v in want.items() if not _same(row.get(f), v)
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

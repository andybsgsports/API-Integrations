"""Nightly SanMar field update onto ADOPTED items, joined by UPC (runs on CI).

Fills the ``custitem_sanmar_*`` fields on every existing item whose upcCode
matches a feed GTIN — availability (total + per-warehouse), pricing
(MAP/MSRP/case), status, and the SanMar keys — plus the NATIVE money/shipping
fields: Base Price = SanMar MSRP, Purchase Price (``cost``) = SanMar piece
price (our cost), ``weight`` = piece weight. Diff-aware: current values are
bulk-read first and only changed fields are written, so steady-state nightly
runs are small. Honors ``SYNC_DRY_RUN``; ``UPDATE_MAX_ITEMS`` caps writes.
"""

from __future__ import annotations

import os
from pathlib import Path

from native_pricing import add_native_diffs, read_base_prices
from warehouse_fields import SANMAR_QTY_FIELDS, SANMAR_WHSE_FIELDS

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.feed_seen import FIELDS as SEEN_FIELDS, stamp
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
    "custitem_sanmar_front_image_url",
] + SANMAR_QTY_FIELDS


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


def build_payloads(
    styles, inventory
) -> tuple[dict[str, dict[str, object]], dict[str, tuple]]:
    """GTIN -> field payload for every feed SKU carrying a barcode, plus
    GTIN -> (base price, cost, weight) for the native-field writes.

    Values are typed (numbers as numbers) and empty values are omitted —
    NetSuite 400s on an empty string in a numeric/currency field.
    """
    whse_by_key: dict[str, str] = {}
    total_by_key: dict[str, int] = {}
    qtys_by_key: dict[str, dict[str, int]] = {}
    unknown_whse: set[str] = set()
    for rec in inventory:
        # One warehouse per line, zero-stock locations hidden -- the
        # semicolon-joined single line was unreadable on item records.
        lines = [
            f"{w.warehouse_label or w.warehouse_no}: {w.quantity:,}"
            for w in rec.warehouses
            if w.quantity
        ]
        whse_by_key[rec.unique_key] = (
            "\n".join(lines) if lines
            else ("0 at all warehouses" if rec.warehouses else "")
        )
        total_by_key[rec.unique_key] = sum(w.quantity for w in rec.warehouses)
        if rec.warehouses:
            # Zero-fill every column so a warehouse that drops out of the
            # feed clears to 0 instead of keeping yesterday's count.
            qtys = {sid: 0 for sid in (s for s, _ in SANMAR_WHSE_FIELDS.values())}
            for w in rec.warehouses:
                hit = SANMAR_WHSE_FIELDS.get(str(w.warehouse_no))
                if hit:
                    qtys[hit[0]] = qtys[hit[0]] + w.quantity
                else:
                    unknown_whse.add(str(w.warehouse_no))
            qtys_by_key[rec.unique_key] = qtys
    if unknown_whse:
        print(f"WARNING: feed warehouse number(s) with no dedicated field "
              f"(still in the text breakdown): {sorted(unknown_whse)}")

    payloads: dict[str, dict[str, object]] = {}
    natives: dict[str, tuple] = {}
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
            for field, wqty in qtys_by_key.get(sku.unique_key, {}).items():
                put(field, wqty)
            images = style.images_by_color.get(sku.color_name)
            # Despite its name, this field holds the BACK-view URL: the front
            # image lives on custitem_atlas_item_image (the real NetSuite
            # Image-type field) instead.
            put("custitem_sanmar_front_image_url", images.back_url() if images else None)
            put("manufacturer", style.brand)  # native Manufacturer = Brand (MILL)
            if entry:
                payloads[sku.gtin] = entry
                natives[sku.gtin] = (
                    None if sku.msrp is None else float(sku.msrp),
                    None if sku.piece_price is None else float(sku.piece_price),
                    None if sku.piece_weight is None else float(sku.piece_weight),
                )
    return payloads, natives


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
    payloads, natives = build_payloads(styles, inventory)
    print(f"feed SKUs with GTIN: {len(payloads):,}")

    client = NetSuiteClient(cfg.netsuite)
    cols = ", ".join(FIELD_ORDER + SEEN_FIELDS)
    gtins = sorted(payloads)
    considered = written = unchanged = priced = failures = 0
    for i in range(0, len(gtins), 250):
        chunk = gtins[i : i + 250]
        in_list = ", ".join(f"'{_sql_escape(g)}'" for g in chunk)
        rows = client.suiteql(
            f"SELECT id, upccode, cost, weight, manufacturer, custitem_ss_brand, {cols} "
            f"FROM item WHERE upccode IN ({in_list})"
        )
        id_list = ", ".join(str(int(r["id"])) for r in rows) or "0"
        base_by_rid = read_base_prices(client, id_list)
        for row in rows:
            gtin = str(row.get("upccode") or "")
            want = payloads.get(gtin)
            if not want:
                continue
            body = {
                f: v for f, v in want.items() if not _same(row.get(f), v)
            }
            # S&S brand wins the Manufacturer field on multi-vendor items.
            if "manufacturer" in body and str(row.get("custitem_ss_brand") or "").strip():
                del body["manufacturer"]
            price, cost, weight = natives.get(gtin, (None, None, None))
            add_native_diffs(
                body, row, base_by_rid, str(row["id"]),
                price=price, cost=cost, weight=weight, same=_same,
            )
            stamp(body, row, "sanmar")
            if not body:
                unchanged += 1
                continue
            if max_items and considered >= max_items:
                continue
            considered += 1
            if "price" in body or "cost" in body or "weight" in body:
                priced += 1
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
          f"unchanged: {unchanged}; price/cost/weight updated: {priced}; "
          f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

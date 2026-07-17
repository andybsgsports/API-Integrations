"""S&S write phase: match + stamp keys/data onto existing items (runs on CI).

Match priority per SKU:

1. **GTIN -> upcCode** — exact item-level barcode join (works because the
   SanMar/Momentec back-fills populated upcCode on shared products).
2. **vendorname + option ids** — style -> items via Vendor Name/Code, child by
   matrix color/size (same machinery as the SanMar/Momentec matchers).

Writes the ``custitem_ss_*`` set and fills ``upcCode`` only where empty,
plus the NATIVE money/shipping fields: Base Price = S&S MSRP, Purchase
Price (``cost``) = S&S customer price (our account cost; falls back to
piece price), ``weight`` = S&S weight. Diff-aware; honors ``SYNC_DRY_RUN``;
``UPDATE_MAX_ITEMS`` caps writes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from native_pricing import add_native_diffs, read_base_prices
from sanmar_netsuite.config import get_config as ns_config
from sanmar_netsuite.netsuite.adopt import COLOR_FIELD, SIZE_FIELD, OptionMaps
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape
from sanmar_netsuite.transform.sizes import normalize_size
from ss_activewear_netsuite.config import get_config as ss_config

FIELDS = [
    "custitem_ss_sku", "custitem_ss_style_id", "custitem_ss_style",
    "custitem_ss_color_name", "custitem_ss_color_code", "custitem_ss_size_name",
    "custitem_ss_gtin", "custitem_ss_brand", "custitem_ss_map",
    "custitem_ss_msrp", "custitem_ss_piece_price", "custitem_ss_dozen_price",
    "custitem_ss_case_price", "custitem_ss_case_size", "custitem_ss_weight",
    "custitem_ss_qty_available", "custitem_ss_qty_by_whse",
    "custitem_ss_is_closeout", "custitem_ss_is_discontinued",
    "custitem_ss_front_image_url", "custitem_ss_on_model_image_url",
]


def _put_into(want: dict[str, object]):
    def put(f: str, v: object) -> None:
        if v is None or (isinstance(v, str) and not v.strip()):
            return
        want[f] = v
    return put


def _same(current, new) -> bool:
    cs = ("" if current is None else str(current)).strip().lower()
    ns_ = str(new).strip().lower()
    if cs == ns_:
        return True
    if isinstance(new, bool):
        return cs in (("t", "true", "1") if new else ("f", "false", "0", ""))
    try:
        return float(cs) == float(ns_)
    except ValueError:
        return False


def _abs_url(path: str | None) -> str | None:
    """S&S image fields carry relative CDN paths; Hyperlink fields need URLs."""
    v = (path or "").strip()
    if not v:
        return None
    if v.startswith(("http://", "https://")):
        return v
    return "https://cdn.ssactivewear.com/" + v.lstrip("/")


def payload_for(p: dict) -> dict[str, object]:
    want: dict[str, object] = {}
    put = _put_into(want)
    # One warehouse per line, zero-stock locations hidden (readability).
    lines = [
        f"{w.get('warehouseAbbr', '')}: {int(w.get('qty') or 0):,}"
        for w in (p.get("warehouses") or [])
        if int(w.get("qty") or 0)
    ]
    whse = "\n".join(lines) if lines else (
        "0 at all warehouses" if p.get("warehouses") else ""
    )
    def num(key):
        v = p.get(key)
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None
    put("custitem_ss_sku", p.get("sku"))
    put("custitem_ss_style_id", p.get("style_id"))
    put("custitem_ss_style", p.get("style_name"))
    put("custitem_ss_color_name", p.get("color_name"))
    put("custitem_ss_color_code", p.get("color_code"))
    put("custitem_ss_size_name", p.get("size_name"))
    put("custitem_ss_gtin", p.get("gtin"))
    put("custitem_ss_brand", p.get("brand_name"))
    put("custitem_ss_map", num("map_price"))
    put("custitem_ss_msrp", num("msrp"))
    put("custitem_ss_piece_price", num("piece_price"))
    put("custitem_ss_dozen_price", num("dozen_price"))
    put("custitem_ss_case_price", num("case_price"))
    case_size = p.get("case_size")
    put("custitem_ss_case_size", int(case_size) if case_size else None)
    put("custitem_ss_weight", num("weight"))
    put("custitem_ss_qty_available", int(p.get("qty_available") or 0))
    put("custitem_ss_qty_by_whse", whse)
    want["custitem_ss_is_closeout"] = bool(p.get("is_closeout"))
    want["custitem_ss_is_discontinued"] = bool(p.get("is_discontinued"))
    put("custitem_ss_front_image_url", _abs_url(p.get("front_image_url")))
    put("custitem_ss_on_model_image_url", _abs_url(p.get("on_model_image_url")))
    return want


def natives_for(p: dict) -> tuple:
    """(base price, cost, weight) for the native-field writes."""
    def num(key):
        v = p.get(key)
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None
    return (num("msrp"), num("customer_price") or num("piece_price"), num("weight"))


def main() -> int:
    allow_write = not ns_config().sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    products_file = Path(ss_config().download_dir) / "products.json"
    products = json.loads(products_file.read_text(encoding="utf-8"))
    print(f"S&S products: {len(products):,}")

    client = NetSuiteClient(ns_config().netsuite)

    # -- pass 1: GTIN -> item id (barcode join)
    by_gtin: dict[str, dict] = {}
    for p in products:
        g = (p.get("gtin") or "").strip()
        if g:
            by_gtin.setdefault(g, p)
    gtins = sorted(by_gtin)
    item_for_gtin: dict[str, str] = {}
    for i in range(0, len(gtins), 300):
        chunk = gtins[i : i + 300]
        in_list = ", ".join(f"'{_sql_escape(g)}'" for g in chunk)
        for row in client.suiteql(
            f"SELECT id, upccode FROM item WHERE upccode IN ({in_list})"
        ):
            item_for_gtin[str(row["upccode"])] = str(row["id"])
    matched: dict[str, dict] = {}  # ns item id -> product
    for g, rid in item_for_gtin.items():
        matched.setdefault(rid, by_gtin[g])
    print(f"barcode matches: {len(matched):,} items")

    # -- pass 2: vendorname + options for products not matched by barcode
    matched_skus = {p.get("sku") for p in matched.values()}
    remaining: dict[str, list[dict]] = {}
    for p in products:
        if p.get("sku") in matched_skus:
            continue
        style = (p.get("style_name") or "").strip()
        if style:
            remaining.setdefault(style, []).append(p)
    options = OptionMaps(client)
    opt_matches = 0
    if options.available:
        for style, plist in remaining.items():
            safe = _sql_escape(style)
            try:
                rows = client.suiteql(
                    f"SELECT id, {COLOR_FIELD} AS color, {SIZE_FIELD} AS size "
                    f"FROM item WHERE vendorname = '{safe}'"
                )
            except Exception:  # noqa: BLE001
                continue
            opt_index = {
                (str(r.get("color") or ""), str(r.get("size") or "")): str(r["id"])
                for r in rows
                if r.get("color") and r.get("size")
            }
            if not opt_index:
                continue
            for p in plist:
                color = (p.get("color_name") or "").strip()
                size_n = normalize_size((p.get("size_name") or "").strip())
                size_ids = list(dict.fromkeys(
                    options.size_candidates(p.get("size_name") or "")
                    + options.size_candidates(size_n)
                ))
                for color_id, _method in options.color_candidates(color, color):
                    hit = next(
                        (opt_index[(color_id, sid)] for sid in size_ids
                         if (color_id, sid) in opt_index),
                        None,
                    )
                    if hit and hit not in matched:
                        matched[hit] = p
                        opt_matches += 1
                        break
    print(f"vendorname+option matches: {opt_matches:,} items")
    print(f"total matched items: {len(matched):,}")

    # -- write phase (diff-aware)
    ids = sorted(matched)
    cols = ", ".join(FIELDS)
    considered = written = unchanged = upc_filled = priced = failures = 0
    for i in range(0, len(ids), 200):
        chunk = ids[i : i + 200]
        in_list = ", ".join(f"'{_sql_escape(x)}'" for x in chunk)
        base_by_rid = read_base_prices(client, in_list)
        for row in client.suiteql(
            f"SELECT id, upccode, cost, weight, {cols} FROM item WHERE id IN ({in_list})"
        ):
            rid = str(row["id"])
            p = matched.get(rid)
            if p is None:
                continue
            want = payload_for(p)
            body = {f: v for f, v in want.items() if not _same(row.get(f), v)}
            gtin = (p.get("gtin") or "").strip()
            if not str(row.get("upccode") or "").strip() and gtin:
                body["upcCode"] = gtin
            price, cost, weight = natives_for(p)
            add_native_diffs(
                body, row, base_by_rid, rid,
                price=price, cost=cost, weight=weight, same=_same,
            )
            if not body:
                unchanged += 1
                continue
            if max_items and considered >= max_items:
                continue
            considered += 1
            if "upcCode" in body:
                upc_filled += 1
            if "price" in body or "cost" in body or "weight" in body:
                priced += 1
            if not allow_write:
                written += 1
                continue
            try:
                client.update_record("inventoryItem", rid, body)
                written += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if failures <= 10:
                    detail = getattr(exc, "payload", "") or getattr(exc, "args", "")
                    print(f"  FAILED item {rid}: {str(exc)[:120]} :: {str(detail)[:400]}")

    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(f"\nss backfill: {verb} {written} item(s); unchanged: {unchanged}; "
          f"upcCode filled (was empty): {upc_filled}; "
          f"price/cost/weight updated: {priced}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

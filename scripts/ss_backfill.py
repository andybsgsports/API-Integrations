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
import time
from pathlib import Path

from native_pricing import add_native_diffs, read_base_prices
from warehouse_fields import SS_QTY_FIELDS, SS_WHSE_FIELDS

from sanmar_netsuite.config import get_config as ns_config
from sanmar_netsuite.netsuite.adopt import COLOR_FIELD, SIZE_FIELD, OptionMaps
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.feed_seen import FIELDS as SEEN_FIELDS
from sanmar_netsuite.netsuite.feed_seen import stamp
from sanmar_netsuite.netsuite.repository import _sql_escape
from sanmar_netsuite.transform.sizes import normalize_size
from ss_activewear_netsuite.config import get_config as ss_config
from ss_activewear_netsuite.ss_activewear.client import SsClient

FIELDS = [
    "custitem_ss_sku", "custitem_ss_style_id", "custitem_ss_style",
    "custitem_ss_color_name", "custitem_ss_color_code", "custitem_ss_size_name",
    "custitem_ss_gtin", "custitem_ss_brand", "custitem_ss_map",
    "custitem_ss_msrp", "custitem_ss_piece_price", "custitem_ss_dozen_price",
    "custitem_ss_case_price", "custitem_ss_case_size", "custitem_ss_weight",
    "custitem_ss_qty_available",
    "custitem_ss_is_closeout", "custitem_ss_is_discontinued",
    "custitem_ss_front_image_url", "custitem_ss_on_model_image_url",
] + SS_QTY_FIELDS

# Feed warehouseAbbr values with no dedicated field, collected during payload
# builds and reported once at the end (they still land in the text breakdown).
UNKNOWN_WHSE: set[str] = set()

# S&S caps this account's API inventory at 500 units per location: any location
# with >=500 on hand reports as exactly 500 (verified via both the REST and
# PromoStandards endpoints -- e.g. a location with 498 comes through as 498 but
# anything >=500 flattens to 500). We can't see past it in code; it's an S&S
# account-entitlement setting. Track how often we hit it so the cap is visible
# in the feed log (real availability may be far higher until S&S lifts it).
INVENTORY_CAP_VALUE = 500
CAP_SKUS: set[str] = set()
CAP_STATS: dict[str, int] = {"locations": 0}


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


def payload_for(p: dict, whse_rows: list[dict] | None = None) -> dict[str, object]:
    """Field payload for one product. ``whse_rows`` is the per-warehouse
    breakdown from the ``/Inventory`` endpoint (the products snapshot itself
    never carries one -- its ``warehouses`` list is always empty)."""
    want: dict[str, object] = {}
    put = _put_into(want)
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
    put("manufacturer", p.get("brand_name"))  # native Manufacturer = Brand
    # S&S publishes mapPrice 0.01 as a "no MAP restriction" placeholder; writing
    # literal pennies onto the item reads as bad data, so treat <=1c as no MAP.
    # (The saved snapshot can still carry the raw 0.01 even though the API parser
    # filters it, so re-apply the rule here on the live-write path.) The write
    # loop's clear-stale-0.01 branch then nulls any placeholder already stored.
    _mp = num("map_price")
    put("custitem_ss_map", None if (_mp is not None and _mp <= 0.011) else _mp)
    put("custitem_ss_msrp", num("msrp"))
    put("custitem_ss_piece_price", num("piece_price"))
    put("custitem_ss_dozen_price", num("dozen_price"))
    put("custitem_ss_case_price", num("case_price"))
    case_size = p.get("case_size")
    put("custitem_ss_case_size", int(case_size) if case_size else None)
    put("custitem_ss_weight", num("weight"))
    put("custitem_ss_qty_available", int(p.get("qty_available") or 0))
    want["custitem_ss_is_closeout"] = bool(p.get("is_closeout"))
    want["custitem_ss_is_discontinued"] = bool(p.get("is_discontinued"))
    put("custitem_ss_front_image_url", _abs_url(p.get("front_image_url")))
    put("custitem_ss_on_model_image_url", _abs_url(p.get("on_model_image_url")))
    if whse_rows is not None:
        # Zero-fill every column so a warehouse that drops out of the feed
        # clears to 0 instead of keeping yesterday's count.
        qtys = {sid: 0 for sid in SS_QTY_FIELDS}
        for w in whse_rows:
            abbr = str(w.get("warehouseAbbr") or "").strip()
            qty = int(w.get("qty") or 0)
            if qty >= INVENTORY_CAP_VALUE:
                CAP_STATS["locations"] += 1
                CAP_SKUS.add(str(p.get("sku") or ""))
            hit = SS_WHSE_FIELDS.get(abbr)
            if hit:
                qtys[hit[0]] += qty
            elif abbr:
                UNKNOWN_WHSE.add(abbr)
        want.update(qtys)
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


def fetch_warehouses(skus: list[str]) -> dict[str, list[dict]]:
    """sku -> raw per-warehouse rows via batched ``/Inventory`` calls.

    Paced between batches to stay under S&S's throttle (the client already
    retries 429/5xx); a failed batch falls back to per-SKU fetches so one bad
    identifier can't drop 39 good ones.
    """
    ss = SsClient(ss_config().ss_api)
    out: dict[str, list[dict]] = {}

    def keep(inv) -> None:
        if inv is not None and inv.sku:
            out[inv.sku] = [
                {"warehouseAbbr": w.warehouse_abbr, "qty": w.qty}
                for w in inv.warehouses
            ]

    step = SsClient.INVENTORY_BATCH_SIZE
    for i in range(0, len(skus), step):
        batch = skus[i : i + step]
        try:
            for inv in ss.iter_inventory(batch):
                keep(inv)
        except Exception:  # noqa: BLE001 - batch failed; retry singly
            for sku in batch:
                try:
                    keep(ss.get_inventory(sku))
                except Exception as exc:  # noqa: BLE001
                    print(f"  inventory fetch failed for {sku}: {str(exc)[:100]}")
        done = min(i + step, len(skus))
        if done % 400 < step or done == len(skus):
            print(f"  warehouse breakdown fetched for {done}/{len(skus)} SKUs "
                  f"({len(out)} returned)")
        time.sleep(0.2)
    return out


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

    # -- per-warehouse availability (only /Inventory carries the breakdown)
    whse_by_sku = fetch_warehouses(
        sorted({str(p.get("sku")) for p in matched.values() if p.get("sku")})
    )

    # -- write phase (diff-aware)
    ids = sorted(matched)
    cols = ", ".join(FIELDS + SEEN_FIELDS)
    considered = written = unchanged = upc_filled = priced = failures = 0
    for i in range(0, len(ids), 200):
        chunk = ids[i : i + 200]
        in_list = ", ".join(f"'{_sql_escape(x)}'" for x in chunk)
        base_by_rid = read_base_prices(client, in_list)
        for row in client.suiteql(
            f"SELECT id, upccode, cost, weight, manufacturer, {cols} "
            f"FROM item WHERE id IN ({in_list})"
        ):
            rid = str(row["id"])
            p = matched.get(rid)
            if p is None:
                continue
            want = payload_for(p, whse_by_sku.get(str(p.get("sku") or "")))
            body = {f: v for f, v in want.items() if not _same(row.get(f), v)}
            # Clear stale 0.01 placeholder MAPs written before the no-MAP rule
            # (REST PATCH null empties the field).
            if "custitem_ss_map" not in want:
                try:
                    if float(row.get("custitem_ss_map")) <= 0.011:
                        body["custitem_ss_map"] = None
                except (TypeError, ValueError):
                    pass
            gtin = (p.get("gtin") or "").strip()
            if not str(row.get("upccode") or "").strip() and gtin:
                body["upcCode"] = gtin
            price, cost, weight = natives_for(p)
            add_native_diffs(
                body, row, base_by_rid, rid,
                price=price, cost=cost, weight=weight, same=_same,
            )
            stamp(body, row, "ss")
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

    if UNKNOWN_WHSE:
        print(f"WARNING: feed warehouse code(s) with no dedicated field "
              f"(still in the text breakdown): {sorted(UNKNOWN_WHSE)}")
    if CAP_STATS["locations"]:
        print(
            f"WARNING: S&S inventory cap hit -- {CAP_STATS['locations']} "
            f"warehouse location(s) across {len(CAP_SKUS)} SKU(s) reported "
            f"exactly {INVENTORY_CAP_VALUE} (S&S caps this account's API "
            f"inventory at {INVENTORY_CAP_VALUE}/location; true on-hand may be "
            f"higher). Ask S&S to enable full inventory visibility to see real "
            f"quantities above {INVENTORY_CAP_VALUE}."
        )
    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(f"\nss backfill: {verb} {written} item(s); unchanged: {unchanged}; "
          f"upcCode filled (was empty): {upc_filled}; "
          f"price/cost/weight updated: {priced}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

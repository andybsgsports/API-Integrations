"""Momentec write phase: stamp keys + data onto matched items (runs on CI).

Re-runs the read-only match, then writes onto every matched existing item the
``custitem_mtec_*`` set (keys, MSRP/cost, case pack, availability incl.
per-warehouse, front image URL) and fills ``upcCode`` ONLY where it is empty —
items already keyed by a SanMar barcode are never re-keyed. Also writes the
NATIVE money/shipping fields: Base Price = Momentec MSRP, Purchase Price
(``cost``) = Momentec cost, ``weight`` = feed weight. Diff-aware and
honors ``SYNC_DRY_RUN``; ``UPDATE_MAX_ITEMS`` caps writes.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from urllib.request import Request, urlopen

from native_pricing import WEIGHT_UNIT_LB, WEIGHT_UNIT_OZ, add_native_diffs, read_base_prices

from momentec_netsuite.adopt import match_momentec
from momentec_netsuite.config import get_config
from momentec_netsuite.feeds import parse_product_data
from sanmar_netsuite.config import get_config as ns_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.feed_seen import FIELDS as SEEN_FIELDS
from sanmar_netsuite.netsuite.feed_seen import stamp
from sanmar_netsuite.netsuite.repository import _sql_escape

FIELDS = [
    "custitem_mtec_item_sku",
    "custitem_mtec_style",
    "custitem_mtec_gtin",
    "custitem_mtec_msrp",
    "custitem_mtec_cost",
    "custitem_mtec_case_size",
    "custitem_mtec_qty_available",
    "custitem_mtec_front_image_url",
    "custitem_mtec_size_guide",
    "custitem_mtec_instock_guaranteed",
    "custitem_mtec_is_closeout",
]

# ASG "Ribbon" values that mean the item is a closeout.
CLOSEOUT_FIELD = "custitem_mtec_is_closeout"


def is_closeout(ribbon: str) -> bool:
    """The ASG feed marks closeouts with a 'Closeout' Ribbon value."""
    return "closeout" in (ribbon or "").lower()

# Momentec's feed carries Brand as a numeric code (per the ASG feed spec's
# Brand definition), so we translate it to the real brand name for the native
# Manufacturer field. Unknown codes are logged, never written as a bare number.
BRAND_NAMES = {
    "10": "Augusta Sportswear",
    "15": "High Five",
    "17": "Holloway",
    "18": "Pacific Headwear",
    "60": "Russell Athletic",
    "80": "Alleson Athletic",
    "81": "Badger",
    "87": "Alleson Athletic",
    "88": "C2",
}
UNKNOWN_BRANDS: set[str] = set()

# The ASG feed supplies weightUnit directly per row (unlike SanMar/S&S, where
# the feed is pounds-only) -- normalize its free-text spelling to NetSuite's
# unit reference ({"id": ...}, see native_pricing).
# Unrecognized units are logged, never guessed.
WEIGHT_UNITS = {
    "lb": WEIGHT_UNIT_LB, "lbs": WEIGHT_UNIT_LB, "pound": WEIGHT_UNIT_LB,
    "pounds": WEIGHT_UNIT_LB,
    "oz": WEIGHT_UNIT_OZ, "ounce": WEIGHT_UNIT_OZ, "ounces": WEIGHT_UNIT_OZ,
}
UNKNOWN_WEIGHT_UNITS: set[str] = set()


def _weight_unit(raw: str) -> dict[str, str] | str:
    """Unit reference for the feed's free-text unit; "" when unrecognized."""
    key = raw.strip().lower()
    if not key:
        return ""
    unit = WEIGHT_UNITS.get(key)
    if unit is None:
        UNKNOWN_WEIGHT_UNITS.add(raw)
        return ""
    return unit


def _weight_lb(
    raw_weight: str, raw_unit: str
) -> tuple[float | None, dict[str, str] | str]:
    """(weight in pounds, unit reference) from the feed's value+unit pair.

    Shipping weight is kept in POUNDS across the whole catalogue (business
    decision 2026-07-29, same as native_pricing.weight_display) -- feed rows
    expressed in ounces are converted, not passed through. A blank or
    unrecognized unit keeps the old behaviour: the raw number is written and
    the unit left untouched (logged via UNKNOWN_WEIGHT_UNITS, never guessed).
    """
    w = _num(raw_weight)
    unit = _weight_unit(raw_unit)
    if w is None:
        return None, ""
    if unit == WEIGHT_UNIT_OZ:
        return round(float(w) / 16.0, 4), WEIGHT_UNIT_LB
    return float(w), unit

# Negotiated invoice discount off Momentec's wholesale price. Per the vendor
# program terms ("Discount is half MSRP less 15% on all stock and custom
# styles"), the feed's ``Cost`` column is the standard wholesale (= half MSRP),
# and our net invoiced cost is that wholesale less 15%. The separate quarterly
# SI tiered rebate is a back-end rebate, NOT an invoice discount, so it is
# deliberately excluded from per-item cost. See docs/VENDOR_PROGRAMS.md.
MOMENTEC_INVOICE_DISCOUNT = 0.15


def _net_cost(wholesale: float | None) -> float | None:
    """Feed wholesale (half MSRP) -> our net invoiced cost (less 15%)."""
    if wholesale is None:
        return None
    return round(wholesale * (1 - MOMENTEC_INVOICE_DISCOUNT), 2)

# Styles Momentec guarantees in stock year-round (all colors/sizes/genders),
# from their "In-Stock Guaranteed" program page. Matched against Parent_SKU.
INSTOCK_GUARANTEED = {
    "410400", "410200", "510000", "412000", "210700", "1426", "1425",
    "560000", "522900", "520000", "512900", "512700", "416400", "416200",
    "416000", "412400", "411900", "411600", "410700", "410300", "216200",
    "211900", "211600", "210400", "210200", "322241", "322240", "1423",
    "212000",
}


def fetch(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=300) as resp, dest.open("wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    return dest


def load_inventory(path: Path) -> dict[str, int]:
    """SKU -> total quantity. Momentec ships from a single warehouse, so the
    total IS the per-warehouse number -- no separate breakdown kept."""
    total: dict[str, int] = {}
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh):
            sku = (row.get("Item_SKU") or "").strip()
            if not sku:
                continue
            try:
                qty = int(float(row.get("Item_Qty") or 0))
            except ValueError:
                continue
            total[sku] = total.get(sku, 0) + qty
    return total


def load_images_by_angle(path: Path, prefix: str) -> dict[str, str]:
    """style_color (e.g. 020000.B080) -> first image URL whose View_Angle
    starts with ``prefix`` (e.g. "front" or "back")."""
    out: dict[str, str] = {}
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh):
            if (row.get("View_Angle") or "").strip().lower().startswith(prefix):
                sc = (row.get("Style_Color") or "").strip()
                if sc and sc not in out:
                    out[sc] = (row.get("Image_Url") or "").strip()
    return out


def _num(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _put_into(want: dict[str, object]):
    def put(f: str, v: object) -> None:
        if v is None or str(v).strip() == "":
            return
        want[f] = v
    return put


def _same(current, new) -> bool:
    cs, ns_ = ("" if current is None else str(current)).strip(), str(new).strip()
    if cs == ns_:
        return True
    try:
        return float(cs) == float(ns_)
    except ValueError:
        return False


def _present_columns(client, candidates: list[str]) -> list[str]:
    """Subset of candidate item columns that actually exist in the account.

    A field that has been added to the code but not yet created in NetSuite
    would otherwise make the whole write-phase ``SELECT`` 400, silently
    stalling the nightly for every item. Probing each once lets the run write
    the fields that do exist and warn about the ones that don't.
    """
    ok: list[str] = []
    for c in candidates:
        try:
            client.suiteql(f"SELECT {c} FROM item WHERE rownum <= 1")
            ok.append(c)
        except Exception:  # noqa: BLE001 - unknown column -> treat as absent
            pass
    return ok


def main() -> int:
    cfg = get_config()
    allow_write = not ns_config().sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    dl = Path(cfg.download_dir)

    styles = parse_product_data([
        fetch(cfg.products_url, dl / "product-data-std-all.csv"),
        fetch(cfg.sublimation_url, dl / "sublimation-product-data-std-all.csv"),
    ])
    inv_total = load_inventory(
        fetch(cfg.inventory_url, dl / "ASG_inventory_data.csv")
    )
    # Back image only: the FRONT view is already shown on the item's main
    # Item Image field (custitem_atlas_item_image), so this field carries the
    # back view instead of duplicating the front.
    back_images = load_images_by_angle(
        fetch(cfg.images_url, dl / "product-images-all.csv"), "back")
    sku_by_id = {k.item_sku: k for s in styles for k in s.skus}
    print(f"feed: {len(sku_by_id):,} SKUs; inventory rows for {len(inv_total):,} SKUs")

    client = NetSuiteClient(ns_config().netsuite)
    report = match_momentec(client, styles)
    print("\n" + report.summary())

    # one write per item; first feed claim wins
    by_item: dict[str, object] = {}
    for r in report.matched:
        by_item.setdefault(r.ns_id, r)

    ids = sorted(by_item)
    present = _present_columns(client, FIELDS)
    missing = [f for f in FIELDS if f not in present]
    if missing:
        print(f"WARNING: custom field(s) not present in account -- skipping "
              f"read+write for these (create them, then re-run): {missing}")
    cols = ", ".join(present + SEEN_FIELDS)
    considered = written = unchanged = upc_filled = priced = failures = 0
    for i in range(0, len(ids), 250):
        chunk = ids[i : i + 250]
        in_list = ", ".join(f"'{_sql_escape(x)}'" for x in chunk)
        rows = client.suiteql(
            f"SELECT id, upccode, cost, weight, weightunit, manufacturer, "
            f"custitem_ss_brand, {cols} FROM item WHERE id IN ({in_list})"
        )
        base_by_rid = read_base_prices(client, in_list)
        for row in rows:
            rid = str(row["id"])
            match = by_item.get(rid)
            sku = sku_by_id.get(match.item_sku) if match else None
            if sku is None:
                continue
            style_color = ".".join(sku.item_sku.split(".")[:2])
            want: dict[str, object] = {}
            put = _put_into(want)
            put("custitem_mtec_item_sku", sku.item_sku)
            put("custitem_mtec_style", match.style)
            put("custitem_mtec_gtin", sku.gtin)
            put("custitem_mtec_msrp", _num(sku.msrp))
            net_cost = _net_cost(_num(sku.cost))
            put("custitem_mtec_cost", net_cost)
            put("custitem_mtec_case_size",
                int(sku.case_pack_qty) if str(sku.case_pack_qty).isdigit() else None)
            put("custitem_mtec_qty_available", inv_total.get(sku.item_sku))
            put("custitem_mtec_front_image_url", back_images.get(style_color))
            guide = sku.size_chart_url
            if guide.startswith("http://"):
                guide = "https://" + guide[len("http://"):]
            put("custitem_mtec_size_guide", guide)
            want["custitem_mtec_instock_guaranteed"] = (
                sku.parent_sku in INSTOCK_GUARANTEED)
            # native Manufacturer = brand NAME (feed gives a numeric code)
            brand_name = BRAND_NAMES.get(sku.brand)
            if brand_name:
                put("manufacturer", brand_name)
            elif sku.brand:
                UNKNOWN_BRANDS.add(sku.brand)

            for mf in missing:
                want.pop(mf, None)
            body = {f: v for f, v in want.items() if not _same(row.get(f), v)}
            # S&S brand wins the Manufacturer field on multi-vendor items.
            if "manufacturer" in body and str(row.get("custitem_ss_brand") or "").strip():
                del body["manufacturer"]
            if not str(row.get("upccode") or "").strip() and sku.gtin:
                body["upcCode"] = sku.gtin
            weight_lb, weight_unit = _weight_lb(sku.weight, sku.weight_unit)
            add_native_diffs(
                body, row, base_by_rid, rid,
                price=_num(sku.msrp), cost=net_cost,
                weight=weight_lb, weight_unit=weight_unit,
                same=_same,
            )
            # Closeout checkbox from the feed Ribbon -- explicit boolean diff
            # (NetSuite returns T/F, which _same can't compare to a bool).
            if CLOSEOUT_FIELD in present:
                closeout = is_closeout(sku.ribbon)
                cur_co = str(row.get(CLOSEOUT_FIELD) or "").strip().upper() in (
                    "T", "TRUE", "YES", "1")
                if cur_co != closeout:
                    body[CLOSEOUT_FIELD] = closeout
            stamp(body, row, "momentec")
            if not body:
                unchanged += 1
                continue
            if max_items and considered >= max_items:
                continue
            considered += 1
            if "upcCode" in body:
                upc_filled += 1
            if any(k in body for k in ("price", "cost", "weight", "weightUnit")):
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
                    print(f"  FAILED item {rid}: {str(exc)[:150]}")

    if UNKNOWN_BRANDS:
        print(f"WARNING: unmapped Momentec brand code(s) -- Manufacturer left "
              f"unchanged for these (add them to BRAND_NAMES): "
              f"{sorted(UNKNOWN_BRANDS)}")
    if UNKNOWN_WEIGHT_UNITS:
        print(f"WARNING: unrecognized Momentec Weight_Unit value(s) -- "
              f"weightUnit left unchanged for these (add them to WEIGHT_UNITS): "
              f"{sorted(UNKNOWN_WEIGHT_UNITS)}")
    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(f"\nmomentec backfill: {verb} {written} item(s); unchanged: {unchanged}; "
          f"upcCode filled (was empty): {upc_filled}; "
          f"price/cost/weight updated: {priced}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

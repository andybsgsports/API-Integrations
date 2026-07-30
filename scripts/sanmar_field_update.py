"""Nightly SanMar field update onto ADOPTED items, joined by UPC (runs on CI).

Fills the ``custitem_sanmar_*`` fields on every existing item whose upcCode
matches a feed GTIN — availability (total + per-warehouse), pricing
(MAP/MSRP/case), status, and the SanMar keys — plus the NATIVE money/shipping
fields: Base Price = the higher of MAP and MSRP, Purchase Price (``cost``) = SanMar piece
price (our cost), ``weight`` = piece weight. Diff-aware: current values are
bulk-read first and only changed fields are written, so steady-state nightly
runs are small. Honors ``SYNC_DRY_RUN``; ``UPDATE_MAX_ITEMS`` caps writes.
"""

from __future__ import annotations

import html
import os
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from concurrent_writes import write_records
from native_pricing import (
    add_native_diffs,
    base_price,
    read_base_prices,
    weight_display,
)
from warehouse_fields import SANMAR_QTY_FIELDS, SANMAR_WHSE_FIELDS

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.feed_seen import FIELDS as SEEN_FIELDS
from sanmar_netsuite.netsuite.feed_seen import stamp
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
    "custitem_sanmar_front_image_url",
] + SANMAR_QTY_FIELDS

# SanMar's sanmar_dip.txt inventory file caps Quantity at 1500 per warehouse
# (documented in the SanMar FTP Integration Guide; raised from 500 in July
# 2025). A warehouse with more than 1500 on hand reports as exactly 1500.
# We use dip.txt (SanMar's recommended inventory source), so we inherit the
# cap; track hits so it's visible in the feed log. Real on-hand above 1500 is
# only available via SanMar's PromoStandards/Web Service inventory API.
INVENTORY_CAP_VALUE = 1500
CAP_SKUS: set[str] = set()
CAP_STATS: dict[str, int] = {"locations": 0}


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


# Checkbox field flagged when an item is currently on sale. Auto-detected at
# runtime (used only if it exists in NetSuite), so it self-enables once created
# -- no sequencing/env needed. The sale-aware Purchase Price below needs no field.
ON_SALE_FIELD = os.environ.get("SANMAR_ON_SALE_FIELD", "custitem_bsg_on_sale").strip()

# Checkbox for BSG's DERIVED closeout rule (see is_closeout): discontinued
# with stock remaining. SanMar itself publishes no usable closeout signal --
# a full scan of 161,271 feed SKUs found 'CloseOut' on exactly one, and the
# old title-prefix rule matched none. Self-enabling like the on-sale flag --
# written only if the field exists in NetSuite.
CLOSEOUT_FIELD = os.environ.get(
    "SANMAR_CLOSEOUT_FIELD", "custitem_sanmar_is_closeout"
).strip()

# NOTE: we deliberately do NOT write NetSuite's native Stock Description.
# It is a legacy field hard-capped at 21 characters -- far too short for the
# marketing copy Store Description carries, so anything we put there is a
# meaningless fragment ("Port Authority Ladies"). Worse, exceeding the cap makes
# NetSuite reject the ENTIRE record PATCH (USER_ERROR), which silently killed
# every other field update on the item: five consecutive runs between
# 2026-07-22 and 2026-07-24 attempted 45,531 items and wrote 0. Store Display
# Name and Store Description go on the matrix PARENTS (see
# sync_store_fields_to_parents) -- NetSuite discards them on children.


def _field_exists(client: NetSuiteClient, scriptid: str) -> bool:
    if not scriptid:
        return False
    try:
        client.suiteql(f"SELECT {scriptid} FROM item WHERE rownum <= 1")
        return True
    except Exception:  # noqa: BLE001 - unknown column -> field not created yet
        return False


def _parse_sale_date(s: object) -> date | None:
    text = str(s or "").strip()
    if not text:
        return None
    text = text.split()[0].split("T")[0]  # drop any time component
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def effective_cost(piece_price, sale_price, sale_start, sale_end, today: date):
    """Return ``(cost, on_sale)``.

    ``cost`` is the active sale price when a genuine, in-window sale is running
    (sale price > 0 and below the regular piece price, and today within any
    start/end dates), else the regular piece price. Evaluated every run, so cost
    reverts automatically when the sale ends.
    """
    reg = None if piece_price is None else float(piece_price)
    if sale_price is None or reg is None:
        return reg, False
    sp = float(sale_price)
    if sp <= 0 or sp >= reg:  # not a genuine discount
        return reg, False
    start, end = _parse_sale_date(sale_start), _parse_sale_date(sale_end)
    if (start and today < start) or (end and today > end):
        return reg, False  # sale not active today
    return sp, True


def _projects(client: NetSuiteClient, col: str) -> bool:
    try:
        client.suiteql(f"SELECT {col} FROM item WHERE rownum <= 1")
        return True
    except Exception:  # noqa: BLE001 - column not queryable
        return False


_STATUS_PREFIX = re.compile(r"^(DISCONTINUED|CLOSEOUT|NEW)\b[\s:–-]*", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


DISCONTINUED_STATUS = "discontinued"


def is_closeout(product_status: str, qty: int | None) -> bool:
    """DERIVED closeout: discontinued but still sellable.

    SanMar publishes no usable closeout signal of its own -- a full scan of
    all 161,271 feed SKUs found PRODUCTSTATUS values Regular (70.0%),
    Discontinued (18.4%), Active (6.9%), New (3.9%), Coming soon (0.8%) and
    'CloseOut' on exactly ONE SKU. The old title-prefix rule matched 0 of
    160,404. So this is a BSG business rule (Andy, 2026-07-29), not vendor
    data: an item SanMar has discontinued while stock remains is being
    cleared out; once the stock reaches zero it is simply gone, not a
    closeout, so it drops back off the flag on the next run.
    """
    if str(product_status or "").strip().lower() != DISCONTINUED_STATUS:
        return False
    return qty is not None and int(qty) > 0


def store_display_name(title: str, style: str) -> str:
    """Clean product name = the feed title with any status prefix and trailing
    style number stripped, Title-cased if it arrived ALL CAPS.

    This deliberately mirrors ``description_update._polish_name`` so the web
    Store Display Name matches the item's Display Name / Description exactly
    (those are set from the same feed title by the description-update job)."""
    name = _STATUS_PREFIX.sub("", _clean(title))
    if style:
        name = re.sub(rf"[\s.,-]*{re.escape(style)}[\s.]*$", "", name, flags=re.I)
    name = name.strip(" .,-")
    if name.isupper():
        name = name.title()
    return name


def sync_store_fields_to_parents(
    client: NetSuiteClient, styles, store_cols: list[str], allow_write: bool
) -> None:
    """Write Store Display Name / Description onto matrix PARENTS.

    NetSuite accepts these fields on a parent and silently discards them on a
    matrix child -- verified live (parent ST841 keeps a PATCHed value; six
    children read blank on both REST and SuiteQL immediately after a
    successful write). Parents are the correct home anyway: the parent is the
    web-store product page, children are size/colour variants.

    A PARENT record does not itself carry ``custitem_sanmar_style`` -- that
    field is written by the UPC-matched child loop above, and matrix parents
    carry no UPC of their own, so querying it directly on the parent found
    only ~50 incidental matches instead of the real ~579 parent styles (first
    live run, 2026-07-29). Fixed the same way ``parent_sync.py`` already
    solved this: read each parent's style from its CHILDREN via NetSuite's
    own ``parent`` link, taking the mode so one stray child can't skew it.
    Diff-aware, and honours dry run.
    """
    want_by_style = {
        s.style: (
            store_display_name(s.title, s.style),
            store_description(s.available_sizes, s.description),
        )
        for s in styles if s.style
    }
    parents = client.suiteql(
        "SELECT id, storedisplayname, storedescription FROM item "
        "WHERE parent IS NULL"
    )
    parents_by_id = {str(p["id"]): p for p in parents}

    # Chunk by parent id (same 250-at-a-time pattern as parent_sync.py) so a
    # single "parent IN (...)" query never approaches SuiteQL's 100,000-row
    # result cap across this catalogue's 150k+ children.
    parent_ids = list(parents_by_id)
    styles_by_parent: dict[str, list[str]] = {}
    for i in range(0, len(parent_ids), 250):
        chunk = parent_ids[i : i + 250]
        in_list = ", ".join(chunk)
        rows = client.suiteql(
            "SELECT parent, custitem_sanmar_style AS sty FROM item "
            f"WHERE parent IN ({in_list}) AND custitem_sanmar_style IS NOT NULL"
        )
        for r in rows:
            pid = str(r.get("parent") or "")
            sty = str(r.get("sty") or "").strip()
            if pid and sty:
                styles_by_parent.setdefault(pid, []).append(sty)

    jobs: list[tuple[str, dict]] = []
    matched = 0
    for pid, row in parents_by_id.items():
        child_styles = styles_by_parent.get(pid)
        if not child_styles:
            continue
        style = Counter(child_styles).most_common(1)[0][0]
        disp, sdesc = want_by_style.get(style, ("", ""))
        matched += 1
        body: dict[str, object] = {}
        if ("storedisplayname" in store_cols and disp
                and not _same(row.get("storedisplayname"), disp)):
            body["storeDisplayName"] = disp
        if ("storedescription" in store_cols and sdesc
                and not _same(row.get("storedescription"), sdesc)):
            body["storeDescription"] = sdesc
        if body:
            jobs.append((pid, body))
    print(f"\nstore fields on parents: {matched:,} of {len(parents):,} "
          f"parent(s) matched to a SanMar style via their children")

    verb = "would update" if not allow_write else "updated"
    if not allow_write:
        print(f"store fields on parents: {len(jobs):,} of {matched:,} "
              f"matched parent(s) {verb} (dry run)")
        return
    p_dropped: dict[str, int] = {}
    written, failures = write_records(
        client, "inventoryItem", jobs, dropped=p_dropped
    )
    print(f"store fields on parents: {verb} {written:,} of {len(jobs):,} "
          f"differing parent(s) ({matched:,} matched to a style); "
          f"failures: {failures}")
    if p_dropped:
        print(f"  fields dropped by NetSuite: {p_dropped}")


def store_description(available_sizes: str, description: str) -> str:
    """Store/Stock Description = the marketing copy, with a real size list
    ("Women's Sizes: S-2XL") prepended when the feed carries one. One-size items
    (no "Sizes:" line) get just the copy. SanMar's flat feed strips commas and
    ships the features as prose (no bullet column exists); the fully punctuated,
    bulleted copy is only available via their content web service."""
    sizes = _clean(available_sizes)
    body = _clean(description)
    # Only prepend a genuine size list, not "One Size" / blank.
    if sizes and ":" in sizes and body:
        return f"{sizes}\n\n{body}"
    return body or sizes


def build_payloads(
    styles, inventory, today: date | None = None, on_sale_field: str = ""
) -> tuple[
    dict[str, dict[str, object]], dict[str, tuple], dict[str, tuple], dict[str, bool]
]:
    """GTIN -> field payload for every feed SKU carrying a barcode, plus
    GTIN -> (base price, cost, weight) for the native-field writes.

    Values are typed (numbers as numbers) and empty values are omitted —
    NetSuite 400s on an empty string in a numeric/currency field.
    """
    today = today or date.today()
    total_by_key: dict[str, int] = {}
    qtys_by_key: dict[str, dict[str, int]] = {}
    # unique_key -> (case_sale_price, each_sale_price, sale_start, sale_end) from dip.
    sale_by_key: dict[str, tuple] = {}
    unknown_whse: set[str] = set()
    for rec in inventory:
        total_by_key[rec.unique_key] = sum(w.quantity for w in rec.warehouses)
        sale_by_key[rec.unique_key] = (
            rec.case_sale_price, rec.each_sale_price, rec.sale_start, rec.sale_end
        )
        if rec.warehouses:
            # Zero-fill every column so a warehouse that drops out of the
            # feed clears to 0 instead of keeping yesterday's count.
            qtys = {sid: 0 for sid in (s for s, _ in SANMAR_WHSE_FIELDS.values())}
            for w in rec.warehouses:
                if w.quantity >= INVENTORY_CAP_VALUE:
                    CAP_STATS["locations"] += 1
                    CAP_SKUS.add(rec.unique_key)
                hit = SANMAR_WHSE_FIELDS.get(str(w.warehouse_no))
                if hit:
                    qtys[hit[0]] = qtys[hit[0]] + w.quantity
                else:
                    unknown_whse.add(str(w.warehouse_no))
            qtys_by_key[rec.unique_key] = qtys
    if unknown_whse:
        print(f"WARNING: feed warehouse number(s) with no dedicated field "
              f"(their qty is not surfaced on the item): {sorted(unknown_whse)}")

    payloads: dict[str, dict[str, object]] = {}
    natives: dict[str, tuple] = {}
    # gtin -> (store display name, store description) from the feed.
    store_by_gtin: dict[str, tuple] = {}
    # gtin -> derived closeout (discontinued AND still has stock). Per SKU,
    # not per style: a style's sizes can differ in both status and stock.
    closeout_by_gtin: dict[str, bool] = {}
    map_seen = sku_total = on_sale_count = 0
    for style in styles:
        disp = store_display_name(style.title, style.style)
        sdesc = store_description(style.available_sizes, style.description)
        for sku in style.skus:
            if not sku.gtin:
                continue
            sku_total += 1
            if sku.map_price is not None:
                map_seen += 1
            entry: dict[str, object] = {}
            put = _make_put(entry)
            # Cost basis = the CASE price (SanMar's by-the-case unit price, i.e.
            # the "Original Price" shown on sanmar.com) -- NOT the single-piece
            # (open-stock) price, which runs ~$1 higher and is what made the
            # Purchase Price read too high. Compare against the case-level sale
            # price for the same reason; fall back to piece-level when a style
            # carries no case data. (The true contract/Program price is lower
            # still but is not in the SFTP feeds.)
            case_sale, each_sale, sale_start, sale_end = sale_by_key.get(
                sku.unique_key, (None, None, "", "")
            )
            regular = sku.case_price if sku.case_price is not None else sku.piece_price
            sale_price = case_sale if case_sale is not None else each_sale
            cost, on_sale = effective_cost(
                regular, sale_price, sale_start, sale_end, today
            )
            if on_sale:
                on_sale_count += 1
            if on_sale_field:
                put(on_sale_field, on_sale)
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
                weight_lb = None if sku.piece_weight is None else float(sku.piece_weight)
                disp_weight, weight_unit = weight_display(weight_lb)
                natives[sku.gtin] = (
                    # Base Price = the higher of MAP and MSRP.
                    base_price(
                        None if sku.msrp is None else float(sku.msrp),
                        None if sku.map_price is None else float(sku.map_price),
                    ),
                    cost,  # effective cost: sale price while on sale, else regular
                    disp_weight,
                    weight_unit,
                )
                store_by_gtin[sku.gtin] = (disp, sdesc)
                # Derived per SKU: discontinued AND still has stock.
                closeout_by_gtin[sku.gtin] = is_closeout(
                    sku.product_status, qty
                )
    # Ground truth on whether SanMar's feed carries MAP at all: value brands
    # (e.g. Gildan) usually have no MAP, so a blank MAP field can be correct.
    print(f"SanMar MAP coverage: {map_seen}/{sku_total} feed SKUs carry a MAP "
          f"price (blank MAP on a no-MAP brand like Gildan is expected)")
    flag = f" (flagged via {on_sale_field})" if on_sale_field else " (On Sale field not created)"
    print(f"SanMar on sale today: {on_sale_count}/{sku_total} SKUs "
          f"-> Purchase Price = sale price{flag}")
    return payloads, natives, store_by_gtin, closeout_by_gtin


def _same(current: object, new: object) -> bool:
    cs, ns_ = _s(current).strip(), str(new).strip()
    if cs == ns_:
        return True
    # Checkboxes: SuiteQL returns "T"/"F", the payload carries a Python bool.
    # Without this branch _same("F", False) compares "F" to "False" and calls
    # every checkbox changed -- which made EVERY run rewrite all ~45k matched
    # items ("unchanged: 0") just to re-send an identical On Sale flag.
    if isinstance(new, bool):
        cl = cs.lower()
        return cl in (("t", "true", "1") if new else ("f", "false", "0", ""))
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
    client = NetSuiteClient(cfg.netsuite)
    # Use the On Sale / Closeout checkboxes only once they exist in NetSuite
    # (self-enabling).
    on_sale_field = ON_SALE_FIELD if _field_exists(client, ON_SALE_FIELD) else ""
    closeout_field = CLOSEOUT_FIELD if _field_exists(client, CLOSEOUT_FIELD) else ""
    payloads, natives, _store_by_gtin, closeout_by_gtin = build_payloads(
        styles, inventory, on_sale_field=on_sale_field
    )
    print(f"feed SKUs with GTIN: {len(payloads):,}")
    if closeout_field:
        n_co = sum(1 for v in closeout_by_gtin.values() if v)
        print(f"closeout flag active ({closeout_field}): {n_co:,} SKU(s) marked closeout")
    # Store Display Name + Store/Stock Description are native fields; write them
    # only where the column is queryable so the diff works (self-enabling).
    store_cols = [
        c for c in ("storedisplayname", "storedescription")
        if _projects(client, c)
    ]
    if store_cols:
        print(f"store fields active: {store_cols}")
    cols = ", ".join(
        FIELD_ORDER + SEEN_FIELDS + store_cols
        + ([closeout_field] if closeout_field else [])
    )
    gtins = sorted(payloads)
    considered = written = unchanged = priced = failures = 0
    _fail_shown = [0]

    def _on_err(rid: str, exc: Exception) -> None:
        _fail_shown[0] += 1
        if _fail_shown[0] <= 10:
            detail = getattr(exc, "payload", "")
            print(f"  FAILED item {rid}: {str(exc)[:150]} :: {str(detail)[:300]}")

    chunks_skipped = 0
    # Fields NetSuite rejected and we retried without, so a single bad value
    # costs that field rather than the whole record. Reported below -- a
    # silently dropped field is exactly what hid for days.
    dropped: dict[str, int] = {}
    for i in range(0, len(gtins), 250):
        chunk = gtins[i : i + 250]
        in_list = ", ".join(f"'{_sql_escape(g)}'" for g in chunk)
        try:
            rows = client.suiteql(
                f"SELECT id, upccode, cost, weight, weightunit, manufacturer, "
                f"custitem_ss_brand, {cols} FROM item WHERE upccode IN ({in_list})"
            )
        except Exception as exc:  # noqa: BLE001 - sustained throttling shouldn't crash
            # the whole run and discard every chunk already written; skip this
            # one (it'll be picked up next run -- diff-aware) and keep going.
            chunks_skipped += 1
            print(f"  SKIPPED chunk starting at {i}: read failed ({str(exc)[:150]})")
            continue
        id_list = ", ".join(str(int(r["id"])) for r in rows) or "0"
        base_by_rid = read_base_prices(client, id_list)
        write_jobs: list[tuple[str, dict]] = []
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
            price, cost, weight, weight_unit = natives.get(gtin, (None, None, None, None))
            add_native_diffs(
                body, row, base_by_rid, str(row["id"]),
                price=price, cost=cost, weight=weight, weight_unit=weight_unit, same=_same,
            )
            # Store Display Name / Description are NOT written here: NetSuite
            # accepts them on a matrix child and silently discards the value.
            # sync_store_fields_to_parents() writes them on the parents, where
            # they actually stick. Stock Description is skipped too -- see the
            # note at the top of this module (21-char cap rejects the record).
            # Closeout checkbox: explicit boolean diff (NetSuite returns T/F,
            # not a Python bool, so _same can't compare it).
            if closeout_field:
                cur_co = str(row.get(closeout_field) or "").strip().upper() in (
                    "T", "TRUE", "YES", "1")
                want_co = closeout_by_gtin.get(gtin, False)
                if cur_co != want_co:
                    body[closeout_field] = want_co
            stamp(body, row, "sanmar")
            if not body:
                unchanged += 1
                continue
            if max_items and considered >= max_items:
                continue
            considered += 1
            if any(k in body for k in ("price", "cost", "weight", "weightUnit")):
                priced += 1
            if not allow_write:
                written += 1
                continue
            write_jobs.append((str(row["id"]), body))

        # Write this chunk's records with bounded concurrency -- individual
        # sequential PATCHes are throttle-bound and can run for hours.
        w, f = write_records(
            client, "inventoryItem", write_jobs, on_error=_on_err, dropped=dropped
        )
        written += w
        failures += f

    if CAP_STATS["locations"]:
        print(
            f"NOTE: SanMar inventory cap -- {CAP_STATS['locations']} warehouse "
            f"location(s) across {len(CAP_SKUS)} SKU(s) reported exactly "
            f"{INVENTORY_CAP_VALUE} (sanmar_dip.txt caps Quantity at "
            f"{INVENTORY_CAP_VALUE}/warehouse; true on-hand may be higher). "
            f"Uncapped depth is only available via SanMar's Web Service / "
            f"PromoStandards inventory API."
        )
    if chunks_skipped:
        print(
            f"NOTE: {chunks_skipped} chunk(s) skipped after their SuiteQL read "
            f"kept failing (sustained NetSuite throttling) -- those items were "
            f"not considered this run; diff-aware, so the next run picks them up."
        )
    # Store Display Name / Description live on the matrix PARENT, not on the
    # children. Proven live 2026-07-29: a PATCH to a child is accepted and
    # silently discarded (REST and SuiteQL both still read blank right after a
    # successful write), while the same PATCH on parent ST841 sticks. Parents
    # are also the right home for it -- the parent is the web-store product
    # page; children are just size/colour variants.
    if store_cols:
        sync_store_fields_to_parents(client, styles, store_cols, allow_write)

    if dropped:
        print("\nWARNING: NetSuite rejected these field(s); the rest of each "
              "record was written without them:")
        for field, n in sorted(dropped.items(), key=lambda kv: -kv[1]):
            print(f"  {field}: dropped on {n:,} record(s)")
    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(f"\nsanmar field update: {verb} {written} item(s); "
          f"unchanged: {unchanged}; price/cost/weight updated: {priced}; "
          f"failures: {failures}; chunks skipped: {chunks_skipped}")
    return 1 if (failures or chunks_skipped) else 0


if __name__ == "__main__":
    raise SystemExit(main())

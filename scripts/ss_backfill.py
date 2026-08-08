"""S&S write phase: match + stamp keys/data onto existing items (runs on CI).

Match priority per SKU:

1. **GTIN -> upcCode** — exact item-level barcode join (works because the
   SanMar/Momentec back-fills populated upcCode on shared products).
2. **vendorname + option ids** — style -> items via Vendor Name/Code, child by
   matrix color/size (same machinery as the SanMar/Momentec matchers).

Writes the ``custitem_ss_*`` set and fills ``upcCode`` only where empty,
plus — ONLY on items whose Preferred Vendor is S&S (see
``pricing_ownership.py``) — the NATIVE money/shipping fields: Base Price =
S&S MSRP, Purchase Price (``cost``) = S&S customer/program price, and
``weight`` = S&S weight.
When S&S is running a sale (``salePrice`` below our regular cost), the
Purchase Price tracks that sale price and the On Sale checkbox
(``custitem_bsg_on_sale``) is ticked -- both revert automatically once the
sale price leaves the feed. Diff-aware; honors ``SYNC_DRY_RUN``;
``UPDATE_MAX_ITEMS`` caps writes.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from concurrent_writes import write_records
from native_pricing import (
    add_native_diffs,
    base_price,
    read_base_prices,
    weight_display,
)
from pricing_ownership import VENDOR_SS, owns_pricing, read_preferred
from run_status import exit_code
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

# Concurrency for the READ phases (SuiteQL lookups + the S&S /Inventory pull).
# These were fully sequential, which made the job's runtime a ~fixed multi-hour
# floor no matter how few items actually changed -- the diff-convergence fix
# only ever sped up the WRITE phase. Reads are far cheaper than writes for both
# APIs, but they still count against NetSuite's concurrency governance, so this
# stays conservative and tunable.
READ_WORKERS = int(os.environ.get("SS_READ_CONCURRENCY", "6") or "6")

# Styles per batched vendorname lookup (pass 2). NetSuite tolerates large IN
# lists; 250 matches the chunk size the other writers use.
STYLE_CHUNK = 250


class _Phase:
    """Print how long each phase took, so a slow nightly names its own culprit.

    Without this the log's print() output is block-buffered through `tee` and
    every line lands with the same end-of-run timestamp -- which is exactly why
    a 4-hour run couldn't be attributed to a phase. Each line is flushed.
    """

    def __init__(self) -> None:
        self.t0 = time.monotonic()

    def mark(self, label: str) -> None:
        now = time.monotonic()
        print(f"  [phase] {label}: {now - self.t0:,.1f}s", flush=True)
        self.t0 = now

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

# On Sale flag: ticked when S&S's sale price undercuts our regular cost. Shared
# with the SanMar side; the write is self-enabling -- skipped until the field
# actually exists in NetSuite (see _field_exists below).
ON_SALE_FIELD = os.environ.get("SS_ON_SALE_FIELD", "custitem_bsg_on_sale").strip()


def _field_exists(client: NetSuiteClient, scriptid: str) -> bool:
    if not scriptid:
        return False
    try:
        client.suiteql(f"SELECT {scriptid} FROM item WHERE rownum <= 1")
        return True
    except Exception:  # noqa: BLE001 - unknown column -> field not created yet
        return False


def effective_cost(regular, sale_price):
    """Return ``(cost, on_sale)`` for one product.

    ``regular`` is our normal cost (customer/program price, or piece price when
    S&S doesn't quote a customer price). S&S publishes ``salePrice`` only while a
    promotion is live -- there is no start/end window in the feed -- so a sale
    price above 0 and below the regular cost is an active discount. Evaluated
    every run, so the cost reverts the moment the sale price drops out.
    """
    reg = None if regular is None else float(regular)
    if sale_price is None:
        return reg, False
    sp = float(sale_price)
    if sp <= 0:
        return reg, False
    if reg is None:
        return sp, False  # no regular to compare against -> use it, don't flag
    if sp >= reg:
        return reg, False  # not a genuine discount
    return sp, True


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
    """(base price, cost, weight, weight_unit, on_sale) for the native-field
    writes.

    Cost follows the S&S sale price while a promotion is live, otherwise our
    customer (program) price, otherwise the piece price. S&S reports weight
    in pounds (see docs/SS_ACTIVEWEAR.md); weight_display picks the more
    natural display unit (ounces under 1 lb) and converts the number to match.
    """
    def num(key):
        v = p.get(key)
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None
    regular = num("customer_price")
    if regular is None:
        regular = num("piece_price")
    cost, on_sale = effective_cost(regular, num("sale_price"))
    disp_weight, weight_unit = weight_display(num("weight"))
    # Base Price = the higher of MAP and MSRP.
    return (base_price(num("msrp"), num("map_price")),
            cost, disp_weight, weight_unit, on_sale)


def fetch_warehouses(skus: list[str]) -> dict[str, list[dict]]:
    """sku -> raw per-warehouse rows via batched ``/Inventory`` calls.

    Batches run CONCURRENTLY: this is a pure read of ~15k SKUs in blocks of 40
    (~370 round trips), and run sequentially it was a fixed multi-minute floor
    on every nightly regardless of how little actually changed. The client
    already retries 429/5xx with exponential backoff, so overshoot self-
    corrects; tune with SS_READ_CONCURRENCY if S&S starts pushing back.

    A failed batch falls back to per-SKU fetches so one bad identifier can't
    drop 39 good ones.
    """
    ss = SsClient(ss_config().ss_api)
    out: dict[str, list[dict]] = {}
    lock = threading.Lock()

    def keep(inv) -> None:
        if inv is not None and inv.sku:
            with lock:
                out[inv.sku] = [
                    {"warehouseAbbr": w.warehouse_abbr, "qty": w.qty}
                    for w in inv.warehouses
                ]

    step = SsClient.INVENTORY_BATCH_SIZE
    batches = [skus[i : i + step] for i in range(0, len(skus), step)]
    done = [0]

    def run(batch: list[str]) -> None:
        try:
            for inv in ss.iter_inventory(batch):
                keep(inv)
        except Exception:  # noqa: BLE001 - batch failed; retry singly
            for sku in batch:
                try:
                    keep(ss.get_inventory(sku))
                except Exception as exc:  # noqa: BLE001
                    print(f"  inventory fetch failed for {sku}: {str(exc)[:100]}")
        with lock:
            done[0] += len(batch)
            n = done[0]
        if n % 400 < step or n >= len(skus):
            print(f"  warehouse breakdown fetched for {n}/{len(skus)} SKUs "
                  f"({len(out)} returned)", flush=True)

    with ThreadPoolExecutor(max_workers=READ_WORKERS) as ex:
        list(ex.map(run, batches))
    return out


def main() -> int:
    allow_write = not ns_config().sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    products_file = Path(ss_config().download_dir) / "products.json"
    products = json.loads(products_file.read_text(encoding="utf-8"))
    print(f"S&S products: {len(products):,}", flush=True)
    print(f"read concurrency: {READ_WORKERS}", flush=True)
    phase = _Phase()

    client = NetSuiteClient(ns_config().netsuite)

    # -- pass 1: GTIN -> item id (barcode join)
    by_gtin: dict[str, dict] = {}
    for p in products:
        g = (p.get("gtin") or "").strip()
        if g:
            by_gtin.setdefault(g, p)
    gtins = sorted(by_gtin)
    item_for_gtin: dict[str, str] = {}
    gtin_lock = threading.Lock()
    gtin_chunks = [gtins[i : i + 300] for i in range(0, len(gtins), 300)]

    def _query_gtins(chunk: list[str]) -> None:
        in_list = ", ".join(f"'{_sql_escape(g)}'" for g in chunk)
        rows = client.suiteql(
            f"SELECT id, upccode FROM item WHERE upccode IN ({in_list})"
        )
        with gtin_lock:
            for row in rows:
                item_for_gtin[str(row["upccode"])] = str(row["id"])

    def match_gtins(chunk: list[str]) -> None:
        # A 429 that outlasts the client's own retry budget must not take the
        # whole run down with it (it did: one bad chunk among ~650 concurrent
        # ones crashed the process 3 minutes in, on the very first live run of
        # this parallel pass -- 2026-07-30, run 30582440348). Falling back to
        # smaller chunks costs that chunk's matches, not the run; the missed
        # items just pick up on the next diff-aware run.
        try:
            _query_gtins(chunk)
            return
        except Exception:  # noqa: BLE001 - chunk failed; retry smaller
            pass
        for i in range(0, len(chunk), 50):
            sub = chunk[i : i + 50]
            try:
                _query_gtins(sub)
            except Exception as exc:  # noqa: BLE001
                print(f"  gtin match failed for a batch of {len(sub)}: "
                      f"{str(exc)[:100]}")

    # ~650 chunks for a 195k-SKU feed -- sequentially that alone was a large
    # slice of the nightly, and it's a pure read.
    with ThreadPoolExecutor(max_workers=READ_WORKERS) as ex:
        list(ex.map(match_gtins, gtin_chunks))
    matched: dict[str, dict] = {}  # ns item id -> product
    for g, rid in item_for_gtin.items():
        matched.setdefault(rid, by_gtin[g])
    print(f"barcode matches: {len(matched):,} items", flush=True)
    phase.mark("pass 1 barcode join")

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
        # One SuiteQL query PER STYLE used to run here, sequentially -- tens of
        # thousands of unmatched products group into thousands of styles, and
        # the whole pass yields a handful of matches (13 on 2026-07-29). That
        # made it the single largest cost in the job. Same query, batched by
        # vendorname IN (...) and run concurrently: thousands of round trips
        # collapse to dozens. A failed chunk falls back to per-style so one bad
        # style name can't drop the rest.
        rows_by_style: dict[str, list[dict]] = {}
        style_lock = threading.Lock()
        style_names = sorted(remaining)
        style_chunks = [
            style_names[i : i + STYLE_CHUNK]
            for i in range(0, len(style_names), STYLE_CHUNK)
        ]

        def _index(rows) -> None:
            with style_lock:
                for r in rows:
                    vn = str(r.get("vendorname") or "")
                    if vn:
                        rows_by_style.setdefault(vn, []).append(r)

        def load_styles(chunk: list[str]) -> None:
            in_list = ", ".join(f"'{_sql_escape(s)}'" for s in chunk)
            try:
                _index(client.suiteql(
                    f"SELECT id, vendorname, {COLOR_FIELD} AS color, "
                    f"{SIZE_FIELD} AS size FROM item "
                    f"WHERE vendorname IN ({in_list})"
                ))
            except Exception:  # noqa: BLE001 - chunk failed; retry per style
                for style in chunk:
                    try:
                        _index(client.suiteql(
                            f"SELECT id, vendorname, {COLOR_FIELD} AS color, "
                            f"{SIZE_FIELD} AS size FROM item "
                            f"WHERE vendorname = '{_sql_escape(style)}'"
                        ))
                    except Exception:  # noqa: BLE001
                        continue

        with ThreadPoolExecutor(max_workers=READ_WORKERS) as ex:
            list(ex.map(load_styles, style_chunks))

        for style, plist in remaining.items():
            rows = rows_by_style.get(style, [])
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
    print(f"vendorname+option matches: {opt_matches:,} items", flush=True)
    print(f"total matched items: {len(matched):,}", flush=True)
    phase.mark("pass 2 vendorname+option")

    # -- per-warehouse availability (only /Inventory carries the breakdown)
    whse_by_sku = fetch_warehouses(
        sorted({str(p.get("sku")) for p in matched.values() if p.get("sku")})
    )
    phase.mark("S&S /Inventory warehouse fetch")

    # -- write phase (diff-aware)
    ids = sorted(matched)
    # custitem_sanmar_style / custitem_mtec_item_sku are read for the
    # pricing-ownership ranking fallback (higher-ranked feeds than S&S).
    cols = ", ".join(
        FIELDS + SEEN_FIELDS + ["custitem_sanmar_style", "custitem_mtec_item_sku"]
    )
    # Tick the On Sale checkbox only once the field exists in NetSuite.
    on_sale_field = ON_SALE_FIELD if _field_exists(client, ON_SALE_FIELD) else ""
    considered = written = unchanged = upc_filled = priced = failures = 0
    deferred = chunks_skipped = 0
    on_sale_count = 0
    diag_shown = [0]
    _fail_shown = [0]

    _fail_other = [0]

    def _on_err(rid: str, exc: Exception) -> None:
        _fail_shown[0] += 1
        if "429" not in str(exc):
            _fail_other[0] += 1
        if _fail_shown[0] <= 10:
            detail = getattr(exc, "payload", "")
            print(f"  FAILED item {rid}: {str(exc)[:120]} :: {str(detail)[:400]}")

    # Fields NetSuite rejected and we retried without -- reported below, since a
    # silently dropped field is exactly the kind of gap that hides for days.
    dropped: dict[str, int] = {}
    for i in range(0, len(ids), 200):
        chunk = ids[i : i + 200]
        in_list = ", ".join(f"'{_sql_escape(x)}'" for x in chunk)
        base_by_rid = read_base_prices(client, in_list)
        pref_by_rid = read_preferred(client, in_list)
        # Sustained throttling on THIS read must not kill the whole run and
        # discard every chunk already written. The parallel read phases each
        # learned this the hard way (PR #88); the write-phase chunk read was
        # the last one still unguarded, and a 429 propagating out of it threw
        # away 48 minutes of S&S work mid-cycle (2026-08-08, run 31261810147).
        # Skip the chunk -- diff-aware, so the next run picks it up.
        try:
            rows = client.suiteql(
                f"SELECT id, upccode, cost, weight, weightunit, manufacturer, {cols} "
                f"FROM item WHERE id IN ({in_list})"
            )
        except Exception as exc:  # noqa: BLE001
            chunks_skipped += 1
            print(f"  SKIPPED chunk starting at {i}: read failed ({str(exc)[:150]})")
            continue
        write_jobs: list[tuple[str, dict]] = []
        for row in rows:
            rid = str(row["id"])
            p = matched.get(rid)
            if p is None:
                continue
            # Native price/cost/weight (and the shared On Sale flag) belong to
            # the item's Preferred Vendor -- on the ~13.5k items SanMar also
            # carries, that is SanMar, and writing S&S numbers over theirs was
            # most of this job's write volume. See pricing_ownership.py.
            owner = owns_pricing(VENDOR_SS, pref_by_rid.get(rid), row)
            want = payload_for(p, whse_by_sku.get(str(p.get("sku") or "")))
            price, cost, weight, weight_unit, on_sale = natives_for(p)
            if on_sale and owner:
                on_sale_count += 1
            if on_sale_field and owner:
                want[on_sale_field] = on_sale
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
            if owner:
                add_native_diffs(
                    body, row, base_by_rid, rid,
                    price=price, cost=cost, weight=weight,
                    weight_unit=weight_unit, same=_same,
                )
            else:
                deferred += 1
            stamp(body, row, "ss", claim_source=owner)
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
                # Convergence diagnostics: the night after a full successful
                # write, ~everything should be unchanged -- yet 2026-07-29 and
                # -30 both re-priced ~14.3k items. Show WHICH native field
                # keeps differing (current -> wanted) for the first few, so the
                # culprit names itself instead of costing another blind night.
                if diag_shown[0] < 8:
                    diag_shown[0] += 1
                    print(f"  DIAG item {rid} diff keys: {sorted(body)}", flush=True)
                    if "price" in body:
                        print(f"    price: current={base_by_rid.get(rid)!r} "
                              f"want={price!r}", flush=True)
                    if "cost" in body:
                        print(f"    cost: current={row.get('cost')!r} "
                              f"want={cost!r}", flush=True)
                    if "weight" in body:
                        print(f"    weight: current={row.get('weight')!r} "
                              f"want={weight!r}", flush=True)
                    if "weightUnit" in body:
                        print(f"    weightUnit: current={row.get('weightunit')!r} "
                              f"want={weight_unit!r}", flush=True)
            if not allow_write:
                written += 1
                continue
            write_jobs.append((rid, body))

        # Write the chunk with bounded concurrency. This loop used to PATCH one
        # record at a time: ~15k sequential round trips is hours of pure
        # latency, and it was the single largest cost in the nightly. SanMar and
        # Momentec were switched to write_records; S&S was the one writer left
        # behind. Same drop-and-retry protection, so one bad field costs that
        # field rather than the whole record.
        w, f = write_records(
            client, "inventoryItem", write_jobs, on_error=_on_err, dropped=dropped
        )
        written += w
        failures += f

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
    flag = f" (flagged via {on_sale_field})" if on_sale_field else " (On Sale field not created)"
    print(f"S&S on sale today: {on_sale_count}/{len(matched)} matched items "
          f"-> Purchase Price = sale price{flag}")
    phase.mark("write phase")
    if dropped:
        print(f"NOTE: field(s) dropped after NetSuite rejected them: {dropped}")
    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(f"\nss backfill: {verb} {written} item(s); unchanged: {unchanged}; "
          f"upcCode filled (was empty): {upc_filled}; "
          f"price/cost/weight updated: {priced}; "
          f"deferred to Preferred Vendor: {deferred}; "
          f"failures: {failures}; chunks skipped: {chunks_skipped}", flush=True)
    return exit_code("ss backfill", failures, _fail_other[0],
                     written + failures, chunks_skipped)


if __name__ == "__main__":
    raise SystemExit(main())

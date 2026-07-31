# The nightly cycle — exact flow and what each run does

Written 2026-07-31. Times are the *scheduled* UTC slots from
`docs/NIGHTLY_SCHEDULE.md`; GitHub's cron routinely fires 1–3 h late, but the
relative order holds because every writer shares the `netsuite-writes`
concurrency group (one writer at a time; a queued run waits, it doesn't run in
parallel).

Scope per Andy: the nightly cycle is **inventory, pricing and item status
only**. The enrichment jobs (description-update, atlas-image-backfill,
parent-sync, sanmar-autocreate, vendor-sublist, color tools) are
manual-dispatch and are not part of the cycle.

## The shared machinery (read this first)

Every writer follows the same pattern:

1. **Fetch the vendor's feed** (SFTP file, REST/SOAP API, or static CSV).
2. **Match feed rows to existing NetSuite items** — never creates items.
   Matching is barcode-first (`gtin -> upccode`), then vendor style + matrix
   color/size options where the barcode misses.
3. **Diff-aware write**: bulk-read the matched items' current values via
   SuiteQL (chunks of 200–300), compute the wanted values, and PATCH **only
   the fields that differ**. An unchanged item costs a read, not a write.
   Steady-state nights are therefore small; a "full pass" only recurs when
   something really changed en masse.
4. **Heartbeat stamp** (`feed_seen.py`): each writer stamps
   `custitem_feed_source` + `custitem_feed_last_seen` on the items its feed
   carries. The date only refreshes once it's ≥3 days old (a third of the
   write volume); item-lifecycle later inactivates items whose stamp goes
   stale past 7 days.
5. **Pricing ownership** (`pricing_ownership.py`, added 2026-07-31): the
   NATIVE fields — Base Price, Purchase Price (`cost`), `weight`,
   `weightUnit`, the shared On Sale checkbox, and the feed-source identity —
   belong to the item's **Preferred Vendor** (decision: Andy, 2026-07-31).
   A writer that matches an item it does not own still updates its own
   `custitem_<vendor>_*` fields and per-warehouse quantities, and keeps the
   heartbeat fresh, but leaves the native pricing fields alone. Items with no
   Preferred Vendor fall back to `vendor_sublist.py`'s agreed ranking
   (SanMar > Momentec > S&S > UA) among the feeds that match the item.
   Before this, SanMar and S&S re-priced the same ~13.5k shared items against
   each other every single night — most of both jobs' write volume, 429
   pressure, and a storefront Base Price that depended on which job ran last.
6. **Failure handling**: writes run with bounded concurrency
   (`concurrent_writes.py`, default 8 threads); the client retries 429/5xx
   with backoff; when NetSuite rejects a record naming a bad field, that
   field is dropped and the record retried once. A chunk whose *read* keeps
   throttling is skipped (diff-aware — next night picks it up). Any failure
   makes the job exit non-zero, which auto-files a GitHub issue with the run
   link.

`SYNC_DRY_RUN` gates all writes; `UPDATE_MAX_ITEMS` caps them.

## 04:00 — UA Back-fill (`ua_backfill.py`)

- **Feed**: DC OneSource PromoStandards SOAP (`/xml/UNDERARMOR`): sellable
  styles, per-style part detail, live inventory, list pricing. Brand: Under
  Armour. ~130 styles present in NetSuite, ~409 matched items.
- **Match**: GTIN → `upccode`, else vendorname + matrix color/size.
- **Writes**: `custitem_ua_part_id/style/gtin/qty_available`; `upcCode` only
  where empty; `manufacturer` = "Under Armour" unless S&S brand present.
  *If it owns pricing* (UA is the lowest-ranked owner, so in practice only
  when Preferred Vendor is explicitly UA): Base Price = feed list price,
  `cost` = list × `UA_COST_PCT`% (40% per the standing config).
- **Inventory**: single UA total in the custom qty field (DC OneSource
  reports one fulfillment location); no per-warehouse fields.

## 04:20 — DCOS Supplier Backfill (`dcos_backfill.py`)

- **Feed**: same DC OneSource SOAP host, looped over four suppliers each
  night: **Champro, Twin City (TCK), Cap America, Mizuno**. Per-supplier
  price-list CSVs in `data/` act as style allowlists.
- **Match**: four passes — GTIN → `upccode`; matrix options; itemid parsed as
  STYLE-COLOR[-SIZE]; single-part styles claim all their items. Handles
  Champro's grouped ranges (`A014-A019`).
- **Writes**: `custitem_<prefix>_part_id/style/gtin/qty_available` per
  supplier; `upcCode` where empty; `manufacturer` unless S&S brand present;
  Champro additionally gets size-guide/fabrics doc links from the NetSuite
  File Cabinet. **No pricing at all** — the price lists are reference-only
  (user request), so this job can never fight anyone over money fields.
- **Heartbeat sources**: `champro`, `tck`, `capamerica`, `mizuno`.

## 04:40 — Champro CSV Back-fill (`champro_csv_backfill.py`)

- **Feed**: `data/champro_products.csv` committed in the repo (manual
  download from champrosports.com; ~29k rows). Exists for the grouped/no-UPC
  Champro SKUs the live DC OneSource feed can't reach (~66 items).
- **Match**: vendor code (`vendorname`) only.
- **Writes**: fills the Champro key fields **only where empty** (DC OneSource
  stays authoritative), `upcCode` where empty, `weight` from the CSV.
  Deliberately **no inventory** (a manual file must never override the live
  feed) and skips any item another supplier has keyed.
- Heartbeat source is also `champro`, so it never fights the DCOS run.

## 05:00 — Momentec Back-fill (`momentec_backfill.py`) — ~25 min

- **Feed**: four public static CSVs from momentecbrands.com /
  augustasportswear.com: standard + sublimation product data, images,
  ASG inventory. Brands: Augusta, High Five, Holloway, Pacific Headwear,
  Russell Athletic, Alleson, Badger, C2.
- **Match**: style token from `Item_SKU` (`029HBM.BLK.2XL` → vendorname
  `029HBM`), then matrix color/size. GTIN only fills empty `upcCode`.
- **Writes**: the `custitem_mtec_*` set (keys, MSRP, net cost, case size,
  qty, image, size guide, in-stock-guaranteed + closeout flags).
  *If it owns pricing* (rank 2 — owns unless SanMar also carries the item or
  Preferred Vendor points elsewhere): Base Price = MSRP, `cost` = wholesale
  × 0.85 (invoice discount), `weight` normalized to pounds + `weightUnit`.
- **Inventory**: single total (one warehouse).

## 06:00 — SanMar Field Update (`sanmar_field_update.py`) — <1 h steady, ~3 h full pass

- **Feed**: SanMar SFTP flat files — `SanMar_SDL_N.csv` (styles/SKUs/pricing)
  + `sanmar_dip.txt` (per-warehouse inventory + sale windows). ~160k feed
  SKUs, ~45k matched items.
- **Match**: UPC only (feed GTIN → `upccode`).
- **Writes**: the `custitem_sanmar_*` set (keys, MAP/MSRP/case price/case
  size, status, total + per-warehouse quantities zero-filled so a dropped
  warehouse clears, image URLs), the derived closeout flag (= discontinued
  AND stock remaining), shop image field, `manufacturer` unless S&S brand
  present. Store Display Name/Description go on matrix **parents** (children
  silently discard them).
  *If it owns pricing* (rank 1 — owns unless Preferred Vendor explicitly
  points elsewhere): Base Price = higher of MAP/MSRP, `cost` = case price
  (sale-aware: tracks an in-window sale price and reverts automatically),
  On Sale checkbox, `weight` + unit.
- **Known cap**: the dip file caps quantity at 1500/warehouse; ~13k SKUs sit
  at the cap. True depth would need SanMar's PromoStandards inventory API.

## 09:30 — S&S Back-fill (`ss_backfill.py`) — ~1 h 50 m (was 4 h before 2026-07-30)

- **Feed**: S&S Activewear REST API — full products snapshot downloaded
  first (`ss-sync download` → `products.json`, ~195k SKUs), then batched
  `/Inventory` calls for per-warehouse depth on the ~15k matched items.
- **Match**: GTIN → `upccode` (works because SanMar/Momentec populated
  upcCode on shared products), else vendorname + matrix options.
- **Writes**: the `custitem_ss_*` set (keys, brand, MAP with the 0.01
  "no-MAP" placeholder filtered, MSRP/piece/dozen/case prices, weight, qty
  total + per-warehouse zero-filled, closeout/discontinued flags, image
  URLs), `upcCode` where empty, `manufacturer` (S&S brand wins on
  multi-vendor items).
  *If it owns pricing* (rank 3 — in practice only where Preferred Vendor is
  S&S, e.g. S&S-only brands): Base Price = higher of MAP/MSRP, `cost` =
  customer/program price (sale-aware), On Sale checkbox, `weight` + unit.
  On the ~13.5k items SanMar also carries, S&S now **defers** — that
  deferral count prints in the run summary.
- **Known cap**: S&S caps this account's API inventory at 500/location.

## 14:00 — Item Lifecycle (`item_lifecycle.py`) — minutes

Runs **last** on purpose: every writer above must have stamped first.

- Reads every item carrying `custitem_feed_source`; never touches unstamped
  items or unparseable dates.
- Stamp older than **7 days** → inactivate. Stamp fresh again (item came
  back) → **reactivate**, and reactivations run first so tonight's writers
  can PATCH returning items.
- **Circuit breaker** per source: if >30% of a source's items would
  inactivate at once, that source's inactivations are skipped (feed outage,
  not mass discontinuation). Reactivations are never suppressed.
- Matrix parents follow their children: all children inactive → parent
  inactivated; any child active → parent reactivated.
- Writes exactly one field: `isInactive`.

## Who may write the native pricing fields (`price`/`cost`/`weight`)

| Writer | Writes pricing when owner | Rank (no-Preferred fallback) |
| --- | --- | --- |
| sanmar-field-update | Base Price, cost, weight, unit, On Sale | 1 — owns unless Preferred points elsewhere |
| momentec-backfill | Base Price, cost, weight, unit | 2 — defers to SanMar |
| ss-backfill | Base Price, cost, weight, unit, On Sale | 3 — defers to SanMar & Momentec |
| ua-backfill | Base Price, cost | 4 — defers to all of the above |
| dcos-backfill | never (reference-only price lists) | — |
| champro-csv-backfill | weight only, Champro-keyed items only | — |
| item-lifecycle | never | — |

Flip an item's Preferred Vendor in NetSuite and pricing ownership follows it
on the next nightly — no code change needed. Point it at a non-feed vendor
and every feed leaves that item's pricing alone.

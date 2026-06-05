# Architecture

## Goals

1. Keep NetSuite's apparel catalog in sync with SanMar with minimal manual work.
2. Be **idempotent** — re-running a sync changes nothing if SanMar didn't change.
3. Be **safe** — never corrupt NetSuite financial/inventory data; fail per-record
   rather than aborting a whole run.
4. Be **operable** — dry-run everything, clear logs, resumable via delta cache.

## Data flow

```
            ┌─────────────────────────── SanMar SFTP (ftp.sanmar.com:2200) ──┐
            │  SanMar_SDL_N.csv   catalog + pricing + image URLs  (nightly)  │
            │  SanMar_EPDD.csv    catalog + bulk inventory        (nightly)  │
            │  sanmar_dip.txt     inventory by warehouse + sale   (hourly)   │
            └───────────────────────────────────────────────────────────────┘
                                   │  paramiko (SSH/SFTP)
                                   ▼
   sanmar/parsers.py  ──►  models.py            (StyleRecord, SkuRecord, InventoryRecord)
                                   │
                                   ▼
   transform/*.py     ──►  NetSuite REST payloads (item / price / availability)
                                   │
                       state/cache.py: hash(payload) seen before?  ── yes ──► skip
                                   │ no
                                   ▼
   netsuite/client.py ──►  SuiteTalk REST (record + SuiteQL), TBA OAuth1 HMAC-SHA256
                                   │
                                   ▼
                       NetSuite matrix items, prices, custom fields, File Cabinet
```

## NetSuite item model

A SanMar **style** maps to a NetSuite **matrix parent** item; each
style/color/size combination maps to a **matrix child** item.

| SanMar | NetSuite |
|--------|----------|
| `STYLE#` (e.g. `K420`) | Matrix parent `itemId`, external id `SANMAR-K420` |
| `UNIQUE_KEY` (e.g. `920331`) | Matrix child external id `SANMAR-920331` |
| `COLOR_NAME` | Color matrix option |
| `SIZE` | Size matrix option |
| `PIECE_PRICE` | Base price level |
| `CASE_PRICE` / `MSRP` | Optional price levels + custom fields |
| `PRODUCT_STATUS = Discontinued` | `isInactive = true` |
| image URLs | custom URL field (default) or File Cabinet file |

`UNIQUE_KEY` = `INVENTORY_KEY` + `SIZE_INDEX`, SanMar's stable per-SKU id. We use
it as the NetSuite external id so upserts are deterministic across runs.

### Initial load vs. ongoing sync

Creating the parent/child **matrix structure** is done once via NetSuite's CSV
Import Assistant (`sanmar-sync export-csv` produces the file). NetSuite's REST
record API is unreliable for *building* matrix option lists, but excellent for
**updating** existing items — so every subsequent run (prices, availability,
status, images) goes through REST keyed by external id. See
[`NETSUITE_SETUP.md`](NETSUITE_SETUP.md).

## Inventory: why custom fields, not on-hand

SanMar's `sanmar_dip.txt` reports how many units **SanMar** holds in each of its
warehouses (Seattle, Cincinnati, Dallas, …). That is supplier availability, not
your owned stock. NetSuite's real `quantityOnHand` is derived from inventory
transactions (receipts, adjustments, fulfillments) and represents inventory you
possess. Writing supplier numbers there would:

- misstate inventory asset value on the balance sheet, and
- break availability/ATP for items you actually stock.

So availability is written to **custom item fields**:

- `custitem_sanmar_qty_available` — total across SanMar warehouses
- `custitem_sanmar_qty_by_whse` — JSON breakdown per warehouse

These drive drop-ship availability checks and reorder logic without touching the
ledger. If you later want true stock mirroring, do it through Inventory
Adjustment transactions (a deliberate, separate project).

## Delta cache

`state/cache.py` is a small SQLite table keyed by `(entity, key)` storing a
SHA-256 of the last-pushed payload and the NetSuite internal id. On each run:

- unchanged payload → skip the API call entirely;
- changed payload → push, then update the hash;
- the memoized internal id lets pricing/inventory syncs target the right record
  without an extra SuiteQL lookup.

This keeps the daily full-catalog run cheap (only true deltas hit NetSuite) and
makes runs resumable.

## Failure handling

- Network calls (SFTP, REST, image fetch) retry with exponential backoff
  (2→4→8→16s) on transient errors and 429/5xx.
- Sync loops catch per-record exceptions, tally them in `SyncResult`, and
  continue — one bad SKU never aborts the catalog. A non-zero failure count sets
  a non-zero process exit code for the scheduler to alert on.

## Scheduling (suggested)

| Job | Time (PT) | Why |
|-----|-----------|-----|
| `download` + `sync-catalog` + `sync-pricing` | ~7:00 AM | after SanMar's 6 AM file build |
| `sync-inventory` + `sync-live-pricing` | hourly, business hours | `sanmar_dip.txt` refreshes hourly |
| Full catalog refresh | end of July | SanMar's annual catalog rebuild + Jan/Mar/May releases |

Run as cron, a Cloud Run job, or a Lambda on an EventBridge schedule — the CLI is
stateless apart from the SQLite delta cache (persist it on a mounted volume / EFS
or rebuild from scratch; a cold cache just means one full push).

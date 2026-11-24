# Production Cutover Runbook

How to promote the supplier integrations from Sandbox (`11319665-sb1`) to
Production (`11319665`) once the sandbox state is signed off. Written while
the sandbox build is fresh — follow it in order; do not skip the ladder.

## What runs today (sandbox)

| Workflow | Script | Writes |
|---|---|---|
| `sanmar-field-update.yml` | `sanmar_field_update.py` | SanMar custom fields, Base Price/cost/weight, back-image URL |
| `ss-backfill.yml` | `ss_backfill.py` | S&S custom fields, upcCode fill, Base Price/cost/weight |
| `momentec-backfill.yml` | `momentec_backfill.py` | Momentec custom fields, upcCode fill, Base Price/cost/weight |
| `ua-backfill.yml` | `ua_backfill.py` | UA custom fields, upcCode fill, Base Price, cost = list x `UA_COST_PCT`% |
| `description-update.yml` | `description_update.py` | Display Name / Sales / Purchase description (plain title, ranked source) |
| `atlas-image-backfill.yml` | `atlas_image_backfill.py` | Real product image (`custitem_atlas_item_image`), per color |
| `parent-sync.yml` | `parent_sync.py` | Matrix parents mirror children (name/desc/vendor/dept/class/image) |
| `vendor-sublist.yml` | — | Vendor ranking sublists |
| One-time setup | `ns_field_setup.py` / `ns_field_create_soap.py` / `ns_field_describe_all.py` | Field definitions + metadata |

## Prerequisites in production

1. **Integration record + TBA token** in the production account with the same
   permission set the sandbox role has (REST web services, SuiteQL,
   SOAP/SuiteTalk, item edit, custom-field setup for the one-time scripts).
2. **Custom fields**: run the one-time chain against production —
   `ns_field_setup.py` (audit) -> `ns_field_create_soap.py` (create missing)
   -> `ns_field_describe_all.py` (Help/Description/Inline Text). All are
   idempotent.
3. **File Cabinet folder**: the image backfill creates "Supplier Item Images"
   automatically if absent — nothing to pre-create.
4. **`custitem_atlas_item_image`** exists in production (it ships with the
   SuiteSuccess Advanced Inventory bundle); verify with `atlas_field_probe.py`.

## Which custom fields production needs (2026-11-20)

Andy, 2026-11-20: the pipeline keeps NetSuite's own fields current (cost and
the vendor line price, Base Price, weight, UPC, names and descriptions, the
item image, new styles / sizes / colours) plus On Sale and Feed Last Seen. The
vendor reference fields (quantities, MAP / MSRP / case prices, status and
closeout flags, image links) are no longer written; see
`scripts/field_policy.py`. `VENDOR_CUSTOM_FIELDS=all` restores them.

So production does not need the 114-field setup in prerequisite 2. A vendor
step skips any kept field the account does not have, and needs only:

| For | Fields |
|---|---|
| Every vendor | `custitem_bsg_on_sale` (Check Box), `custitem_feed_last_seen` (Date), `custitem_feed_source` (Free-Form Text) |
| SanMar | `custitem_sanmar_unique_key`, `custitem_sanmar_style` (Free-Form Text) |
| S&S | `custitem_ss_sku`, `custitem_ss_style`, `custitem_ss_brand` (Free-Form Text) |
| Momentec | `custitem_mtec_item_sku`, `custitem_mtec_style` (Free-Form Text) |
| Under Armour and the other DC OneSource vendors | `custitem_<vendor>_part_id`, `custitem_<vendor>_style` (Free-Form Text) |

The vendor identifiers are how the Preferred Vendor ranking, the image step
and the description step tell which feed an item belongs to. They are written
once and never change. Without them a step still runs; those later steps just
cannot place the item.

## Credential swap

Production credentials go in **new** GitHub Actions secrets — do not
overwrite the sandbox ones (the sandbox pipeline should keep working):

- `NETSUITE_PROD_ACCOUNT_ID` = `11319665`
- `NETSUITE_PROD_CONSUMER_KEY` / `..._CONSUMER_SECRET`
- `NETSUITE_PROD_TOKEN_ID` / `..._TOKEN_SECRET`

Then point workflows at production by swapping the secret names in the
"Write .env" step (or parameterize with an `environment` input).

## The production workflow (added 2026-11-13)

`production-pipeline.yml` ("Production Vendor Pipeline (manual)") runs one
vendor step against production using the `NETSUITE_PROD_*` secrets. Manual
dispatch only: no schedule, no chain, no item-lifecycle.

- `mode: dry_run` (default) sets `SYNC_DRY_RUN=true` **and**
  `NETSUITE_READ_ONLY=true`. In read-only mode the client refuses every
  request that could change NetSuite (anything but a GET or a SuiteQL query;
  RESTlet calls and SOAP writes included) before it is sent.
- `mode: live` needs `confirm_live` typed as `LIVE` and the production matrix
  RESTlet ids in `NETSUITE_PROD_MATRIX_SCRIPT_ID` /
  `NETSUITE_PROD_MATRIX_DEPLOY_ID` (repository variables or secrets). Caps
  default to 25 styles / 25 items.
- The production RESTlet named "BSG Hive Matrix" is a different script. The
  pipeline needs `suitescript/bsg_sanmar_matrix.js` installed in production
  (docs/RESTLET_DEPLOY.md).

The sandbox nightly and its secrets are not touched by this workflow.

## Production safeguards (added 2026-11-18)

- **Price guard** (`scripts/price_guard.py`, always on in the production
  workflow). Before an update writes, a read-only pass counts how far Base
  Price and Purchase Price would move. If more than a quarter of a vendor's
  existing prices move more than 25%, Base Price and Purchase Price are held
  for that vendor and the run says `PRICE HOLD`; every other field is still
  written. Filling a blank price is not a move. Fewer than 20 compared prices
  never trips it. To release a hold Andy has reviewed, run again with
  `price_hold_override` typed as `RELEASE`. Covers SanMar, S&S, Momentec and
  Under Armour (the writers that set price or cost). Expect the first
  production SanMar update to trip it: Base Price moving to MSRP is a large
  move on many items, and that first move should be looked at before release.
- **Daily summary** (`scripts/post_daily_summary.py`). Each production run
  adds its totals, every item skipped or failed by name, and the price guard
  result to one GitHub issue per day, "Production pipeline daily summary
  <date>". The same text is on the run's summary page. The sandbox nightly
  writes the run summary page only.
- **Write limit**. Production runs use 2 parallel writes (`write_concurrency`
  input), not the sandbox's 8. The production integration record allows 4
  requests at once; 2 leaves half for staff and the other integrations
  (Andy, 2026-11-20).
- **Output is no longer lost when a job is cut off**: phase scripts run
  unbuffered.

In the sandbox the price guard is off unless a manual run sets
`price_guard: on` (the check adds a read pass).

## Added 2026-11-18 (second change)

- **Vendor line Purchase Price upkeep** (`scripts/vendor_line_price_sync.py`,
  last script of the SanMar, Momentec, Under Armour and S&S update steps).
  Sets the Preferred Vendor line's Purchase Price to the item's cost where it
  is blank or differs, only on items whose Preferred Vendor is that step's
  vendor. Other vendor lines are not changed. Capped at 5,000 items per vendor
  per run (`VENDOR_LINE_MAX_ITEMS`), differing lines before blank ones; skipped
  while a price hold is active.
- **SanMar update time budget** (`UPDATE_TIME_BUDGET_MIN`, 290). The update
  stops cleanly before the 350-minute job limit, prints its totals and how
  much it did not reach, and starts from a different place each day so the
  same items are not the ones missed every night.
- **Fields changed line**. SanMar, S&S, Momentec and Under Armour updates
  print which fields they changed and on how many items.
- **SFTP host key pin**. The client reads the server's public key from
  `data/sanmar_sftp_host_key.b64` when `SANMAR_SFTP_HOST_KEY` is not set. The
  "SanMar SFTP Host Key Probe" workflow prints the key to put in that file.

## The gate

Every sandbox workflow writes `NETSUITE_ALLOW_PRODUCTION_WRITES=false` into
`.env`. The client refuses to CONNECT to a non-`-sb` account while it is
false (reads included), so the production workflow sets it to true and relies
on `NETSUITE_READ_ONLY` to keep a dry run from writing.
Flipping it to `true` **is** the cutover switch — change it only per-workflow,
only when that workflow's production ladder (below) is green.

## Promotion sequence (per workflow, in this order)

Run the same ladder used throughout the sandbox build — in production the
stakes are real orders, so no step is optional:

1. **Dry run** (`DRY_RUN=true`, `MAX_ITEMS=25`) — sanity-check the match
   counts and samples against expectations. Production catalogs differ from
   sandbox (sandbox is a point-in-time copy); expect drift, investigate
   anything wildly off.
2. **Live smoke** (`DRY_RUN=false`, `MAX_ITEMS=25`) — then spot-check the
   written items in the production UI.
3. **Full run** (`MAX_ITEMS=0`).
4. Only then enable that workflow's nightly schedule for production.

Order the workflows: field setup -> SanMar -> Momentec -> S&S -> UA ->
description update -> image backfill -> parent sync (same dependency order
as the sandbox build; the copy/image/parent steps read what the supplier
backfills wrote).

## Rollback notes

- All writers are diff-aware and field-scoped: a bad value is corrected by
  re-running with fixed code, not by restoring backups.
- Base Price / cost / weight / upcCode are the only NATIVE fields any writer
  touches (plus the copy fields and the two image fields). Everything else
  is `custitem_*` and can be bulk-cleared without touching core data.
- The File Cabinet images live under "Supplier Item Images" and can be
  deleted as a folder if ever needed; item references clear with them.

## Known cautions

- Sandbox refreshes wipe sandbox state; after any refresh, re-run the
  one-time field setup chain in sandbox before other workflows.
- Two live writers running at the same time can collide on the same record
  ("Record has been changed", benign) — the staggered nightly schedule
  avoids this; keep manual full runs staggered too.
- SuiteQL caps any single query at 100k rows; every writer already chunks,
  keep that pattern in new queries.

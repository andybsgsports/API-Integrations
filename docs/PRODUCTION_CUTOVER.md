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

## Credential swap

Production credentials go in **new** GitHub Actions secrets — do not
overwrite the sandbox ones (the sandbox pipeline should keep working):

- `NETSUITE_PROD_ACCOUNT_ID` = `11319665`
- `NETSUITE_PROD_CONSUMER_KEY` / `..._CONSUMER_SECRET`
- `NETSUITE_PROD_TOKEN_ID` / `..._TOKEN_SECRET`

Then point workflows at production by swapping the secret names in the
"Write .env" step (or parameterize with an `environment` input).

## The gate

Every workflow writes `NETSUITE_ALLOW_PRODUCTION_WRITES=false` into `.env`.
The client refuses writes against a non-`-sb` account while it is false.
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

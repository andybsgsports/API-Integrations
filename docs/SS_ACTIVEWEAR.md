# S&S Activewear → NetSuite

Sibling integration to SanMar — pulls products/pricing/inventory from the
S&S Activewear REST API (`api.ssactivewear.com/v2`) and upserts them into
NetSuite as matrix inventory items.

## Architecture in one paragraph

S&S exposes a REST API (HTTP Basic auth) that returns JSON for catalog,
inventory, and pricing on demand. We pull either the full catalog or a
single style, transform each product into a NetSuite payload (with
`SS-`-prefixed external ids so SanMar and S&S items don't collide), and
PUT them via the same NetSuite client/TBA the SanMar integration uses.
The two integrations share the NetSuite transport but have independent
config (`SS_*` env vars), independent custom fields (`custitem_ss_*`),
and independent external-id namespaces.

## Configuration

Two new env values are required (everything else has defaults — see
`.env.example`):

```dotenv
SS_API_ACCOUNT_NUMBER=07548
SS_API_KEY=<UUID from S&S Account Manager / Freshservice ticket>
```

The same NetSuite TBA credentials drive both SanMar and S&S syncs.
Add `NETSUITE_SS_VENDOR_ID` once you create an S&S vendor record in
NetSuite.

## Custom item fields (22 in NetSuite)

`Customization → Lists, Records, & Fields → Item Fields → New`. Same
"Applies To Inventory Item, Store Value" pattern as the SanMar fields.

| Script ID | Label | Type |
|-----------|-------|------|
| `custitem_ss_sku` | S&S SKU | Free-Form Text |
| `custitem_ss_style_id` | S&S Style ID | Free-Form Text |
| `custitem_ss_style` | S&S Style | Free-Form Text |
| `custitem_ss_color_name` | S&S Color | Free-Form Text |
| `custitem_ss_color_code` | S&S Color Code | Free-Form Text |
| `custitem_ss_size_name` | S&S Size | Free-Form Text |
| `custitem_ss_size_order` | S&S Size Order | Integer |
| `custitem_ss_gtin` | S&S GTIN | Free-Form Text |
| `custitem_ss_brand` | S&S Brand | Free-Form Text |
| `custitem_ss_map` | S&S MAP | Currency |
| `custitem_ss_msrp` | S&S MSRP | Currency |
| `custitem_ss_piece_price` | S&S Piece Price | Currency |
| `custitem_ss_dozen_price` | S&S Dozen Price | Currency |
| `custitem_ss_case_price` | S&S Case Price | Currency |
| `custitem_ss_case_size` | S&S Case Size | Integer |
| `custitem_ss_weight` | S&S Weight (lb) | Decimal |
| `custitem_ss_qty_available` | S&S Qty Available | Integer |
| `custitem_ss_qty_by_whse` | S&S Qty by Warehouse | Long Text |
| `custitem_ss_is_closeout` | S&S Closeout | Checkbox |
| `custitem_ss_is_discontinued` | S&S Discontinued | Checkbox |
| `custitem_ss_front_image_url` | S&S Image URL | Free-Form Text |
| `custitem_ss_on_model_image_url` | S&S On-Model Image URL | Free-Form Text |

## CLI

```bash
# 1. Verify S&S credentials work (single GET, no NetSuite involvement)
ss-sync test-auth

# 2. Snapshot the full catalog locally (caches to downloads/ss/products.json)
ss-sync download

# 3. Generate the matrix-item import CSV for the one-time NetSuite Import Assistant load
ss-sync export-csv --file downloads/ss/products.json --out data/ss_matrix_items.csv

# 4. Ongoing updates via REST
ss-sync sync-catalog    # upsert items
ss-sync sync-pricing    # update price levels + custom price fields
ss-sync sync-inventory  # update qty available + per-warehouse breakdown

# All in one
ss-sync all
```

`SYNC_DRY_RUN=true` (default) makes every "sync-*" command log payloads
without calling NetSuite. The production guardrail (refuses to write to
non-sandbox NetSuite realms unless `NETSUITE_ALLOW_PRODUCTION_WRITES=true`)
applies to both integrations.

## Warehouse availability format

`custitem_ss_qty_by_whse` stores the per-warehouse breakdown as a
semicolon-delimited string: `IL:600;TX:400;NV:234`. Compact, greppable,
survives copy/paste between records. Total `qty_available` is the sum.

## Why not write to `quantityOnHand`?

S&S `qty` is *S&S's* inventory in their warehouses, not yours. Writing
it to NetSuite's `quantityOnHand` would misstate your inventory asset
value. Availability lands on the custom fields above; actual stocked
inventory remains driven by your own goods-received transactions. (Same
architectural call as SanMar.)

## Network egress

The integration needs outbound HTTPS to `api.ssactivewear.com`. In Claude
Code on the web sandboxes, that host must be added to the environment's
network egress allowlist before `ss-sync` commands work; locally there is
no restriction.

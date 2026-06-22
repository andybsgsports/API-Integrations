# NetSuite Setup

One-time configuration in NetSuite before the integration can write. Do this in
a **sandbox** first (account id like `1234567_SB1`).

## 1. Enable features

`Setup > Company > Enable Features`:

- **Items & Inventory** → *Matrix Items* (required), *Multiple Units of Measure*
  (optional, for case/each).
- **SuiteCloud** → *SOAP Web Services* / *REST Web Services*, *Token-Based
  Authentication*.

## 2. Integration record (consumer key/secret)

`Setup > Integration > Manage Integrations > New`:

- Name: `SanMar Sync`
- State: Enabled
- Authentication: check **Token-Based Authentication**; uncheck TBA
  authorization flow / OAuth 2.0 unless you need them.
- Save and copy the **Consumer Key** and **Consumer Secret** (shown once).

→ `.env`: `NETSUITE_CONSUMER_KEY`, `NETSUITE_CONSUMER_SECRET`

## 3. Role + access token (token id/secret)

1. Create or reuse a role with permissions: *Items* (Full), *Lists > Vendors*
   (View), *Documents and Files* (Full, for image upload), *SuiteScript* /
   *REST Web Services*, *Log in using Access Tokens*.
2. Assign the role to an integration user.
3. `Setup > Users/Roles > Access Tokens > New` → pick the integration app, user,
   and role. Copy the **Token ID** and **Token Secret** (shown once).

→ `.env`: `NETSUITE_TOKEN_ID`, `NETSUITE_TOKEN_SECRET`,
`NETSUITE_ACCOUNT_ID` (your realm, e.g. `1234567` or `1234567_SB1`).

## 4. Accounts, subsidiary, vendor

Collect internal ids (enable `Home > Set Preferences > Show Internal IDs`):

- Subsidiary new items belong to → `NETSUITE_SUBSIDIARY_ID` (OneWorld only)
- Income / Asset / COGS accounts for new inventory items →
  `NETSUITE_INCOME_ACCOUNT_ID`, `NETSUITE_ASSET_ACCOUNT_ID`,
  `NETSUITE_COGS_ACCOUNT_ID`
- A Vendor record for **SanMar** → `NETSUITE_SANMAR_VENDOR_ID`

## 5. Price levels

`Setup > Accounting > Price Levels`. The **Base Price** (id `1`) receives the
piece price. Optionally create:

- `SanMar Case` → `NETSUITE_PRICE_LEVEL_CASE`
- `MSRP` → `NETSUITE_PRICE_LEVEL_MSRP`

Leave the env vars blank to skip those levels.

## 6. Custom item fields

`Customization > Lists, Records, & Fields > Item Fields > New`. Create these
(applies to: Inventory Item; check *Matrix* sub-items where relevant). The
script ids must match `.env` (defaults shown):

| Field id | Label | Type |
|----------|-------|------|
| `custitem_sanmar_unique_key` | SanMar Unique Key | Free-Form Text |
| `custitem_sanmar_inventory_key` | SanMar Inventory Key | Free-Form Text |
| `custitem_sanmar_size_index` | SanMar Size Index | Free-Form Text |
| `custitem_sanmar_style` | SanMar Style | Free-Form Text |
| `custitem_sanmar_mf_color` | SanMar Mainframe Color | Free-Form Text |
| `custitem_sanmar_gtin` | SanMar GTIN | Free-Form Text |
| `custitem_sanmar_map` | SanMar MAP | Currency |
| `custitem_sanmar_msrp` | SanMar MSRP | Currency |
| `custitem_sanmar_case_price` | SanMar Case Price | Currency |
| `custitem_sanmar_case_size` | SanMar Case Size | Integer |
| `custitem_sanmar_status` | SanMar Product Status | Free-Form Text |
| `custitem_sanmar_qty_available` | SanMar Qty Available | Integer |
| `custitem_sanmar_qty_by_whse` | SanMar Qty by Warehouse | Long Text |
| `custitem_sanmar_front_image_url` | SanMar Image URL | Free-Form Text (or Hyperlink) |
| `custitem_sanmar_image` | SanMar Image | Image (File Cabinet) |

Index `custitem_sanmar_unique_key` (mark *Store Value*) so the SuiteQL lookups
are fast.

## 7. Matrix options (Color, Size)

Matrix items need two list/record item-option fields used as matrix axes:

- **Color** — custom list or Free-Form, marked as a *Matrix Option*.
- **Size** — custom list or Free-Form, marked as a *Matrix Option*.

The CSV import map (next step) maps the SanMar `Color` / `Size` columns onto
these option fields.

## 8. Initial matrix-item load (CSV Import Assistant)

```bash
sanmar-sync export-csv --file downloads/SanMar_SDL_N.csv --out data/matrix_items.csv
```

The export writes **one child-matrix row per SKU** in BSG's matrix
import-template format. Each row carries `Parent/Child Matrix Item` =
`Child Matrix Item` and `Subitem Of` = the style, so NetSuite nests them under
the parent instead of creating standalone items. Column conventions match BSG's
live items: `Vendor Name/Code` = style, `Display Name/Code` = product title,
`Base Price` = SanMar MSRP, accounts by name (`SALES OF MERCHANDISE` /
`COST OF MERCHANDISE SOLD` / `INVENTORY`), `Class` mapped from SanMar category
(`Knit Shirts` → `Tops : Polos`, etc.), `Department` = `Apparel`. Income
account and tax schedule come from `SYNC_INCOME_ACCOUNT` / `SYNC_TAX_SCHEDULE`.

**Prerequisite:** the parent matrix item must already exist and list each SKU's
color/size in its grid. The REST API *cannot* create matrix children (the matrix
option fields are read-only), so this load goes through the Import Assistant.

Then `Setup > Import/Export > Import CSV Records`:

- Import Type: **Items**, Record Type: **Inventory Item**.
- Data Handling: **Add or Update**.
- Map columns straight across (the headers match BSG's matrix template):
  `Parent/Child Matrix Item` → Matrix Type, `Subitem Of` → Subitem Of,
  `Matrix Attribute 1 - Size` / `Matrix Attribute 2 - Color` → the matrix
  option fields, accounts/class/department/etc. to their fields.
- Save the map as `SanMar Matrix Items` so you can re-run it.

NetSuite nests each child under its parent style via the matrix options. After
this load, ongoing updates flow through the REST syncs by external id.

## 9. Image folder (only if uploading images into NetSuite)

`Documents > Files > File Cabinet` → create a folder (e.g. `SanMar Images`),
note its internal id, and pass it to `sanmar-sync sync-images --folder <id>`.
Default behavior stores the SanMar CDN URL on `custitem_sanmar_front_image_url`
and uploads nothing.

## 10. Smoke test

```bash
# Verify auth + a single record, still safe via low cap
SYNC_DRY_RUN=false SYNC_MAX_RECORDS=2 sanmar-sync sync-catalog --file downloads/SanMar_SDL_N.csv
```

Check the two items in NetSuite, then remove the cap and schedule `all`.

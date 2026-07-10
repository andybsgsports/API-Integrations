# NetSuite Setup — Step-by-Step for First-Timers

A click-by-click companion to `NETSUITE_SETUP.md`. Read this if you have not
configured a NetSuite integration before. **Do all of this in your sandbox
account** (the URL contains `_SB1` or `-sb1`). Nothing here can affect
production data as long as you stay in the sandbox URL.

> Tip — keep this open in one window and your sandbox in another. After each
> section you should have one more value to paste into the project's `.env`
> file.

---

## 0. Log in to the sandbox

1. Open the **sandbox** URL — the account id will end in `_SB1`. If you're
   not sure which URL is the sandbox, log into your normal NetSuite, click
   the user-menu in the top-right, and choose **Switch Role** → pick a role
   on the sandbox company. The URL bar should change to include `-sb1`.
2. The account id you'll need for `.env` is shown under
   **Setup → Company → Company Information → Account Id**. Copy it now.

→ **`.env`**: `NETSUITE_ACCOUNT_ID=` *(paste sandbox account id, e.g. `1234567_SB1`)*

While you're in Preferences, also do this so internal IDs are visible
everywhere:

- **Home → Set Preferences → General tab → Defaults section → Show Internal
  IDs** → ✅ check it → **Save**.

---

## 1. Turn on the features the integration needs

**Setup → Company → Enable Features**

Find each tab/feature, check the box, **Save** between tabs if prompted.

| Tab | Feature | Why |
|-----|---------|-----|
| Items & Inventory | **Matrix Items** | SanMar styles come in size × color matrices |
| Items & Inventory | **Multiple Units of Measure** *(optional)* | only if you want case vs. each |
| SuiteCloud | **SOAP Web Services** | required to use SuiteTalk |
| SuiteCloud | **REST Web Services** | this integration uses REST |
| SuiteCloud | **Token-Based Authentication** | how we authenticate without a password |
| SuiteCloud | **SuiteScript Server Scripts** | needed by some admin scripts later |

Accept the legal terms popup if any feature shows one. **Save**.

---

## 2. Create the Integration Record (gives you the Consumer Key + Secret)

**Setup → Integration → Manage Integrations → New**

Fill in exactly:

| Field | Value |
|-------|-------|
| Name | `SanMar Sync` |
| State | **Enabled** |
| Description | *(optional, e.g. "SanMar → NetSuite product/pricing/inventory sync")* |
| Note | leave blank |

**Authentication tab:**

| Setting | Value |
|---------|-------|
| Token-Based Authentication | ✅ check |
| TBA: Authorization Flow | ❌ uncheck |
| Authorization Code Grant | ❌ uncheck |
| Client Credentials (Machine to Machine) | ❌ uncheck |
| User Credentials | ❌ uncheck |

Click **Save**.

**A green banner appears showing Consumer Key and Consumer Secret. Copy
both NOW — they are shown only once.** If you miss them you have to reset
and start over.

→ **`.env`**: `NETSUITE_CONSUMER_KEY=...` and `NETSUITE_CONSUMER_SECRET=...`

---

## 3. Create a Role for the integration user

You can reuse an existing role, but a dedicated role is cleaner and easier
to audit later.

**Setup → Users/Roles → Manage Roles → New**

Fill in:

| Field | Value |
|-------|-------|
| Name | `SanMar Sync Role` |
| Subsidiary Restrictions | **Allow Cross-Subsidiary Record Viewing** ✅ *(if OneWorld)* |
| Center Type | leave default |
| Single Sign-on Only | ❌ unchecked |

**Permissions sub-tab → Setup** subtab, add (search by name, set the level):

| Permission | Level |
|------------|-------|
| Access Token Management | Full |
| Log in using Access Tokens | Full |
| REST Web Services | Full |
| User Access Tokens | Full |

**Permissions sub-tab → Lists** subtab, add:

| Permission | Level |
|------------|-------|
| Items | Full |
| Vendors | View |
| Documents and Files | Full |
| Subsidiaries | View |
| Locations | View |
| Accounts | View |
| Price Books *(if enabled)* | View |

**Save.**

---

## 4. Create the integration user (if you don't already have one)

**Setup → Users/Roles → Manage Users → New**

Use a real email (not a personal one) — anything that goes to an admin
distribution list works. Make the user **inactive for UI login** but allow
**Access Token Login**.

| Field | Value |
|-------|-------|
| Name | `SanMar Sync (Integration User)` |
| Email | *your integrations distribution email* |
| Send Notification Email | ❌ uncheck |
| Access tab → Give Access | ✅ |
| Access tab → Manually assign or change password | ✅ (set a long random one — won't be used) |
| Access tab → Require Password Change On Next Login | ❌ uncheck |
| Roles sublist → add `SanMar Sync Role` | ✅ |

**Save.**

---

## 5. Create the Access Token (gives you Token ID + Secret)

**Setup → Users/Roles → Access Tokens → New**

| Field | Value |
|-------|-------|
| Application Name | **SanMar Sync** *(the integration record from step 2)* |
| User | **SanMar Sync (Integration User)** *(from step 4)* |
| Role | **SanMar Sync Role** *(from step 3)* |
| Token Name | *(auto-filled — leave it)* |

Click **Save**.

**A banner appears showing Token ID and Token Secret. Copy both NOW —
shown only once.**

→ **`.env`**: `NETSUITE_TOKEN_ID=...` and `NETSUITE_TOKEN_SECRET=...`

---

## 6. Look up the internal IDs the integration needs

You can find each of these one of two ways:

- **Easy way**: navigate to the list, hover over the link in the URL bar,
  look for `id=12345`.
- **Power user way**: paste this SuiteQL query into
  **Customization → SuiteAnalytics → SuiteQL** and copy the results:

```sql
SELECT 'subsidiary' AS type, id, name FROM subsidiary WHERE isinactive = 'F'
UNION ALL
SELECT 'vendor', id, companyname FROM vendor
  WHERE companyname LIKE '%SanMar%' AND isinactive = 'F'
UNION ALL
SELECT 'account', id, acctnumber || ' ' || fullname FROM account
  WHERE accounttype IN ('Income','OtherCurrentAsset','CostOfGoodsSold')
  AND isinactive = 'F'
UNION ALL
SELECT 'pricelevel', id, name FROM pricelevel WHERE isinactive = 'F'
ORDER BY type, id;
```

You're looking for these:

| `.env` key | What it is |
|------------|-----------|
| `NETSUITE_SUBSIDIARY_ID` | the subsidiary new SanMar items live under (OneWorld accounts only — leave blank otherwise) |
| `NETSUITE_SANMAR_VENDOR_ID` | the **Vendor** record for SanMar — create one at *Lists → Relationships → Vendors → New* if it doesn't exist |
| `NETSUITE_INCOME_ACCOUNT_ID` | the income (sales) account that should be the default for new SanMar items |
| `NETSUITE_ASSET_ACCOUNT_ID` | the inventory asset account for new SanMar items |
| `NETSUITE_COGS_ACCOUNT_ID` | the COGS account for new SanMar items |
| `NETSUITE_PRICE_LEVEL_BASE` | the **Base Price** level (almost always `1`) |
| `NETSUITE_PRICE_LEVEL_CASE` | *(optional)* a price level you create called "SanMar Case" |
| `NETSUITE_PRICE_LEVEL_MSRP` | *(optional)* a price level you create called "MSRP" |

> If you don't know which account is the right one to use — ask whoever owns
> your chart of accounts. **Don't guess** on accounts: a wrong income
> account here just shows up on every SanMar invoice.

---

## 7. Create the custom item fields

There are 15 custom fields the integration writes to. They're how we keep
the SanMar-specific data (style/color/size codes, MAP, MSRP, availability
counts) on the NetSuite item record.

**Customization → Lists, Records, & Fields → Item Fields → New** for each
row below. Fields shared across all 15:

- **Subtypes → Inventory Item**: ✅ checked (others can stay unchecked unless you want them).
- **Applies To**: leave default unless noted.
- **Store Value**: ✅ checked.
- **Display → Subtab**: `Main` (or create a *SanMar* subtab if you want).
- **Validation & Defaulting → Default Value**: leave blank.

Then per row:

| ID (script id) | Label | Type | Notes |
|----------------|-------|------|-------|
| `custitem_sanmar_unique_key` | SanMar Unique Key | Free-Form Text | Mark **Mandatory** ❌ no, but check **Show as Inventory Item Search Field** — speeds up our lookups |
| `custitem_sanmar_inventory_key` | SanMar Inventory Key | Free-Form Text | — |
| `custitem_sanmar_size_index` | SanMar Size Index | Free-Form Text | — |
| `custitem_sanmar_style` | SanMar Style | Free-Form Text | — |
| `custitem_sanmar_mf_color` | SanMar Mainframe Color | Free-Form Text | — |
| `custitem_sanmar_gtin` | SanMar GTIN | Free-Form Text | — |
| `custitem_sanmar_map` | SanMar MAP | Currency | — |
| `custitem_sanmar_msrp` | SanMar MSRP | Currency | — |
| `custitem_sanmar_case_price` | SanMar Case Price | Currency | — |
| `custitem_sanmar_case_size` | SanMar Case Size | Integer Number | — |
| `custitem_sanmar_status` | SanMar Product Status | Free-Form Text | — |
| `custitem_sanmar_qty_available` | SanMar Qty Available | Integer Number | — |
| `custitem_sanmar_qty_by_whse` | SanMar Qty by Warehouse | Long Text | This holds a JSON blob, so it must be Long Text |
| `custitem_sanmar_front_image_url` | SanMar Image URL | Free-Form Text | Set max length 500 |
| `custitem_sanmar_image` | SanMar Image | Image | Only needed if you want NetSuite to host the image. Skip for now if you're starting on URL-only. |

**Tedious, yes — 15 forms. But you only do it once.** Verify after each
save: the script id you typed appears at the top of the saved field, e.g.
*ID: `custitem_sanmar_unique_key`*. If NetSuite added a numeric suffix
(`custitem_sanmar_unique_key2`) you typed the ID wrong — delete and redo
it, otherwise the integration will silently write to the wrong field.

→ **`.env`**: keep the defaults from `.env.example` unless you renamed
any field. If you did rename, paste the actual script ids into the
`NS_FIELD_SANMAR_*` keys.

---

## 8. Confirm matrix-axis fields exist (Color, Size)

Matrix items need two list/option fields used as the matrix axes. NetSuite
ships **Color** and **Size** built-in for many roles, but if you don't see
them in **Customization → Lists, Records, & Fields → Item Option Fields**,
create:

| Field id | Label | Type | Matrix Option |
|----------|-------|------|---------------|
| `custcol_color` | Color | Free-Form Text *(or list — see below)* | ✅ |
| `custcol_size` | Size | Free-Form Text *(or list — see below)* | ✅ |

Why Free-Form vs List: SanMar has hundreds of color names. Free-Form is the
fastest start. If you want strict lists later, convert these to
list/record reference fields and create a custom list with the SanMar
colors / standard sizes — that's a refactor for later, not now.

---

## 9. File Cabinet folder (only if you want images stored in NetSuite)

**Documents → Files → File Cabinet**

1. Pick or create a folder (e.g. `Images/SanMar`).
2. Click the folder and copy the **Internal ID** from the URL.
3. You don't put this in `.env` — you pass it on the CLI when you run image
   sync: `sanmar-sync sync-images --folder 12345`.

Skip this entirely if you're only going to store the SanMar CDN image URL
on each item (the default — that lives in `custitem_sanmar_front_image_url`).

---

## What you should have in `.env` after this guide

```dotenv
# from step 0
NETSUITE_ACCOUNT_ID=1234567_SB1

# from step 2
NETSUITE_CONSUMER_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NETSUITE_CONSUMER_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# from step 5
NETSUITE_TOKEN_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NETSUITE_TOKEN_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# from step 6
NETSUITE_SUBSIDIARY_ID=1
NETSUITE_SANMAR_VENDOR_ID=2345
NETSUITE_INCOME_ACCOUNT_ID=678
NETSUITE_ASSET_ACCOUNT_ID=345
NETSUITE_COGS_ACCOUNT_ID=789
NETSUITE_PRICE_LEVEL_BASE=1
NETSUITE_PRICE_LEVEL_CASE=
NETSUITE_PRICE_LEVEL_MSRP=

# from step 7 — leave at defaults unless you renamed any custom field
# (don't paste these if you didn't change them)

# SAFETY — keep both of these as-is until first sandbox test passes
SYNC_DRY_RUN=true
NETSUITE_ALLOW_PRODUCTION_WRITES=false
```

---

## Sanity check before first sync

```bash
# 1. Just download the SFTP files — no NetSuite write involved
sanmar-sync download

# 2. Dry-run the catalog — parses + logs payloads, makes ZERO NetSuite calls
sanmar-sync sync-catalog

# 3. Generate the matrix-item import CSV for the one-time NetSuite Import Assistant load
sanmar-sync export-csv --file downloads/SanMar_SDL_N.csv --out data/matrix_items.csv
```

If step 1 fails: SFTP credentials. If step 2 fails: parser/config — check the
log message, it'll name the missing field. If step 3 produces a CSV with
sensible columns, you're ready for the **one-time CSV import** in
**Setup → Import/Export → Import CSV Records** (see `NETSUITE_SETUP.md`
step 8 for that flow).

After the CSV import, ongoing updates flow through the REST sync commands.

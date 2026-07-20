# CSV auto-create: adding items the vendors carry but NetSuite doesn't

The nightly REST syncs are **update-only** — they keep prices, availability, and
status current on items NetSuite already has, but they never create anything.
This is the companion path that **creates the missing items** from the vendor
feeds, using NetSuite's native **CSV Import** (the only reliable way to build
true matrix items).

Two kinds of "missing", handled separately:

| Kind | Example | Path |
| --- | --- | --- |
| **New child** under a parent NetSuite already has | Style `PC61` gains a new colour "Neon Green" | Child CSV import — **ready now** |
| **Net-new parent** style NetSuite doesn't carry at all | Style `PC90` doesn't exist in NetSuite | Parent must be created first, then children — later phase |

The generator keeps these in two files so the child import is always clean (a
child row whose parent doesn't exist yet fails the import).

---

## How it flows

```
SanMar SDL feed ──┐
                  ├─> scripts/sanmar_create_preview.py (read-only diff)
live NetSuite  ───┘        │
                           ├─> data/sanmar_new_children.csv   (new colours/sizes under existing parents)
                           └─> data/sanmar_new_parents.txt    (styles that need a parent first)

data/sanmar_new_children.csv ──> bsg_csv_import RESTlet ──> N/task.CsvImportTask ──> matrix children created
                                  (against a saved import map, "Add" mode)
```

The diff step is **read-only** and safe to run any time (workflow
**SanMar Create Preview**, or bump `.sanmar-create-preview-trigger`). It never
writes to NetSuite — it only reports counts and stages the CSV.

---

## Naming cheat-sheet — use these exact values

So the repo secrets, the RESTlet, and the import map all line up, use these names
verbatim. The **Type in the ID field** column is exactly what to key into
NetSuite's *ID* box; the **Stored as** column is what NetSuite saves it as.

| Thing | Name/Title | Type in the ID field | Stored as |
| --- | --- | --- | --- |
| RESTlet file (File Cabinet) | `bsg_csv_import.js` | — | — |
| Script record | `BSG CSV Import Runner` | `_bsg_csv_import` | `customscript_bsg_csv_import` |
| Script **Deployment** | `BSG CSV Import Runner` | `_bsg_csv_import` | `customdeploy_bsg_csv_import` |
| File Cabinet folder | `BSG Imports` | — | (note its internal id) |
| Saved import map | `BSG SanMar New Children (Add)` | `custimport_bsg_sanmar_child` | `custimport_bsg_sanmar_child` |

**How NetSuite's ID field works — read this so the ids come out right:**

- On a **Script record** and a **Script Deployment**, the *ID* field
  auto-prefixes. Key in exactly **`_bsg_csv_import`** (leading underscore, no
  `customscript`) and NetSuite saves the Script as `customscript_bsg_csv_import`
  and the Deployment as `customdeploy_bsg_csv_import`. If you instead type the
  full `customscript_bsg_csv_import`, NetSuite accepts it as-is — either way the
  stored id is the same. Just don't type `customscript…` *and* leave the leading
  underscore, or you'll get a doubled prefix.
- Once saved, the ID is **permanent** — you can't rename it. If you fat-finger
  it, delete the record and recreate. So set it deliberately now.
- The **saved import map** ID does *not* auto-prefix. Type the full
  **`custimport_bsg_sanmar_child`** exactly, including the `custimport_` prefix.
- Lowercase, letters/digits/underscores only; no spaces. These exact ids are
  what you put in the four secrets in Step 7, so copy them from here.

---

## Step 1 — Upload the RESTlet file

1. **Documents > Files > File Cabinet.**
2. Open (or create) the **SuiteScripts** folder.
3. **Add File** → upload `suitescript/bsg_csv_import.js` from this repo. Leave
   *Available Without Login* unchecked.

## Step 2 — Create the Script record

1. **Customization > Scripting > Scripts > New.**
2. In **Script File**, pick the `bsg_csv_import.js` you just uploaded → **Create
   Script Record**.
3. NetSuite reads the file's `@NScriptType` and shows a **RESTlet** record. Fill
   in:
   - **Name:** `BSG CSV Import Runner`
   - **ID:** type `_bsg_csv_import` (NetSuite stores it as
     `customscript_bsg_csv_import`)
   - **Description** (paste this so the next admin knows what it is):

     > Runs a saved NetSuite CSV Import against an inline CSV supplied by the BSG
     > catalog automation. SuiteTalk has no "run a saved CSV import" call, so
     > this thin RESTlet is the bridge: POST a job `{ mappingId, csv, folderId }`
     > and it writes the CSV to the File Cabinet, submits the import against the
     > named saved map (which decides Add vs Update), and returns the async task
     > id. GET `?taskId=<id>` reports that task's status. It never builds record
     > payloads itself — the saved map is the single source of truth for the
     > mapping and create/update behaviour. Used by the SanMar "new children"
     > create-only import. See docs/CSV_AUTOCREATE.md.

   - On the **Scripts** subtab, confirm **GET Function** = `get` and **POST
     Function** = `post` (they auto-fill from the file's `return` block).
   - **Save.**

## Step 3 — Deploy the script

1. On the saved script record, open the **Deployments** subtab → **New
   Deployment** (or the **Deploy Script** button).
2. Fill in:
   - **Title:** `BSG CSV Import Runner`
   - **ID:** type `_bsg_csv_import` (stored as `customdeploy_bsg_csv_import`)
   - **Status:** `Released`
   - **Log Level:** `Debug` (turn down to `Error` once it's proven)
   - **Roles:** add the exact role your integration token uses (or
     `Administrator` to start).
3. **Save.** At the bottom of the deployment record, the **External URL**
   contains `script=…&deploy=…` — those two numbers (or the `customscript…` /
   `customdeploy…` ids) are what go in the secrets below.

## Step 4 — Role permissions

The role your token uses (Setup > Users/Roles > Manage Roles) needs, on the
**Setup** subtab:

- **Import CSV File** — *Full* (this is the permission that lets the RESTlet
  submit an import; without it every job fails `INSUFFICIENT_PERMISSION`).
- **SuiteScript** — *Full*.

Plus, on the **Lists** subtab: **Items** = *Full* and the two matrix **Custom
Lists** (Color, Size) = *Edit* or better.

## Step 5 — Create the File Cabinet folder for staged CSVs

The RESTlet writes each incoming CSV to a folder before importing it.

1. **Documents > Files > File Cabinet > New Folder.**
2. **Name:** `BSG Imports`. Parent = *SuiteScripts* (or top level). **Save.**
3. Open the folder and read its **internal id** from the URL
   (`…folder=<id>`) — that's `NETSUITE_CSVIMPORT_FOLDER_ID`.

## Step 6 — Create the saved CSV import map (the important one)

Do this once with a real sample so the columns line up. Run the preview first
(workflow **SanMar Create Preview**) so `data/sanmar_new_children.csv` exists,
download it from the branch, and use it as the import file below.

1. **Setup > Import/Export > Import CSV Records.**
2. **Step "Scan & Upload File":**
   - **Import Type:** `Items`
   - **Record Type:** `Inventory Item`
   - **Character Encoding:** `Unicode (UTF-8)` (our files are UTF-8)
   - **CSV Column Delimiter:** `Comma`
   - **Select** the `sanmar_new_children.csv` file. **Next.**
3. **Step "Import Options":**
   - **Data Handling:** **Add** — create-only. (Add never overwrites an existing
     record, so a stray already-present row is skipped, not clobbered — belt and
     braces on top of the diff.)
   - Leave **Overwrite Missing Fields** *unchecked*.
   - **Advanced Options** (expand): leave **Run Server SuiteScript and Trigger
     Workflows** at its default; **Validate Mandatory Custom Fields** = on. **Next.**
4. **Step "Field Mapping":** NetSuite auto-maps most columns because our headers
   match the field labels. Set/confirm each row per the table below. The three
   that always need a manual touch are **Subitem Of** (match by Internal ID), the
   two **Matrix Attribute** columns (the matrix option fields), and the
   **Vendor 1 …** pair (the item's Vendor sublist).
5. **Step "Save mapping & Start Import":**
   - **Import Map Name:** `BSG SanMar New Children (Add)`
   - **ID:** `custimport_bsg_sanmar_child`
   - **Save & Run** for the first validation batch (or **Save** only and let the
     RESTlet run it).

### Full field mapping (CSV column → NetSuite field)

| CSV column | Map to NetSuite field | Reference / notes |
| --- | --- | --- |
| `External ID` | External ID | The item's external id (`SANMAR-<key>`). Set on create; also the key the REST syncs use afterward. |
| `Item Name/Number` | Name (Item Name/Number) | The unique child name `style-Colour-Size`. |
| `Display Name/Code` | Display Name/Code | Product title only (no colour/size). |
| `Vendor Name/Code` | Vendor Name/Code | The vendor's code for the item = the style. |
| `Parent/Child Matrix Item` | Matrix Type | Value is `Child Matrix Item` → maps to the "child" matrix type. |
| `Subitem Of` | Subitem Of (Parent) | **Match by Internal ID.** The merge export writes the parent's internal id here, so numeric styles like `2000` resolve to the right parent instead of colliding with an id. |
| `Matrix Attribute 1 - Size` | the **Size** matrix option field (`custitem_bsg_size`) | Match the custom-list value **by name** (`Small`, `Large`, …). |
| `Matrix Attribute 2 - Color` | the **Color** matrix option field (`custitem_bsg_color`) | Match the custom-list value **by name** (`Black`, `True Red`, …). |
| `UPC Code` | UPC Code | The SKU GTIN. |
| `Description` | Description | Item/vendor description. |
| `Units Type` | Units Type | `Each`. |
| `Stock Units` | Stock Unit | `Eaches`. |
| `Purchase Units` | Purchase Unit | `Eaches`. |
| `Sale Units` | Sale Unit | `Eaches`. |
| `Subsidiary` | Subsidiary | Match by name/path (`Parent Company : Badger Sporting Goods Company`). OneWorld only. |
| `Include Children` | Include Children | `TRUE` (subsidiary rollup). |
| `Department` | Department | `Apparel`. |
| `Class` | Class | Match by full path (`Tops : Tees`). Blank when the SanMar category is unmapped. |
| `Location` | Location | `Badger Sporting Goods`. |
| `Costing Method` | Costing Method | `Average`. |
| `Purchase Price` | Purchase Price | SanMar piece price (our cost). |
| `Vendor 1 Name` | **Vendors** sublist → Vendor | Match by name (`Sanmar Corp`). This is the item-vendor sublist line, not a body field. |
| `Vendor 1 Purchase Price` | **Vendors** sublist → Purchase Price | Same sublist line as above. |
| `Weight` | Weight | Piece weight. |
| `COGS Account` | COGS Account | Match accounts **by number** (`5100`). |
| `Income Account` | Income Account | Match by number (`4100`). |
| `Asset Account` | Asset Account | Match by number (`1200`). |
| `Tax Schedule` | Tax Schedule | `Taxable`. |

Pricing (Base Price) columns are intentionally **absent** — NetSuite's matrix
child importer rejects them ("Please enter missing price(s)"). Base Price and the
income account are applied to the new children **after** the import by
`sanmar-sync reconcile-items` (same post-step the original load uses; see
NETSUITE_SETUP.md §8).

> **Matrix-grid caveat.** A child import only succeeds for a colour/size whose
> value already exists in the matrix custom lists *and* is available on the
> parent's grid. A brand-new colour on an existing style may need that colour
> added to the parent's matrix options first. The first small test import (next
> section) is what surfaces this — if a row errors with an invalid/unavailable
> matrix option, add the value to the parent grid and re-run.

## Step 7 — Add the repo secrets

Repo **Settings > Secrets and variables > Actions**:

```
NETSUITE_CSVIMPORT_SCRIPT_ID   # customscript_bsg_csv_import (or its numeric id)
NETSUITE_CSVIMPORT_DEPLOY_ID   # customdeploy_bsg_csv_import (or its numeric id)
NETSUITE_CSVIMPORT_FOLDER_ID   # internal id of the "BSG Imports" folder (Step 5)
NETSUITE_CSVIMPORT_CHILD_MAP   # custimport_bsg_sanmar_child (Step 6)
```

---

## Validate by hand once, before automating

1. Run the **SanMar Create Preview** workflow. Read the report it commits
   (`data/sanmar_create_preview.txt`) — it says how many new children exist and
   how many styles are net-new.
2. Open `data/sanmar_new_children.csv`, keep the header + the first ~5 rows in a
   scratch copy, and import that by hand through the wizard (Step 6) using the
   saved map. Confirm the 5 children land under the right parents with the right
   colour/size, external id, cost, and vendor.
3. Run `sanmar-sync reconcile-items` (dry run first) to see Base Price + income
   account applied to them.

Only once that hand-import is clean should the RESTlet drive the whole file.

## Wiring the automated import (phase B)

The RESTlet accepts the CSV **inline** (`csv` raw text, or `csvBase64`), so the
pipeline hands over the file it just generated in a single signed REST call — no
separate SOAP upload. Submit shape (one job per map):

```json
{ "jobs": [ {
    "mappingId": "custimport_bsg_sanmar_child",
    "csv": "External ID,Item Name/Number,...\n...",
    "folderId": 771,
    "fileName": "sanmar_new_children_2026-07-20.csv",
    "name": "SanMar new children 2026-07-20"
} ] }
```

The RESTlet returns `{ "results": [ { "taskId": "...", "fileId": ..., "ok": true } ] }`.
Poll `GET ?taskId=<id>` until `status` is `COMPLETE`, then run
`sanmar-sync reconcile-items` to apply prices to the new children.

**Ordering caveat (net-new parents):** a child import fails if its parent doesn't
exist. The preview keeps net-new-parent rows *out* of the child CSV for exactly
this reason. When the parent-creation phase is added, its parent import must
finish (poll to `COMPLETE`) **before** those children are submitted — CSV imports
are async/queued, so submitting both at once doesn't guarantee order.

---

## Safety

- The diff/preview never writes to NetSuite.
- The saved map is **Add** mode: create-only, never an update or overwrite.
- Everything stays sandbox-gated — `NETSUITE_ALLOW_PRODUCTION_WRITES=false` in
  every workflow until the production cutover (see PRODUCTION_CUTOVER.md).
- Start with the small hand-import above to confirm the map before letting the
  RESTlet drive the full file.

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

## One-time NetSuite setup (admin)

### 1. Deploy the import RESTlet

`suitescript/bsg_csv_import.js` submits a CSV import against a saved map and
reports its status. SuiteTalk has no "run a saved CSV import" call, so this thin
RESTlet is the bridge.

1. **Upload** `suitescript/bsg_csv_import.js` to the File Cabinet (SuiteScripts).
2. **Create the Script record** (Customization > Scripting > Scripts > New) →
   type **RESTlet**. Confirm **POST** = `post` and **GET** = `get`. Note the
   **Script ID** and, on **Deployments > New Deployment**, the **Deployment ID**
   (Status = `Released`).
3. **Permissions** for the integration role: **Import CSV File** (Setup),
   **SuiteScript**, plus **Inventory Item** and the matrix **Custom Lists**.

### 2. Create the saved CSV import map(s)

Setup > Import/Export > **Import CSV Records**, using
`data/sanmar_new_children.csv` (produced by the preview) as the sample so the
column headers line up:

- **Import Type** = Items, **Record Type** = Inventory Item.
- **Data Handling** = **Add** — create-only. (Add never overwrites an existing
  record, so a stray already-present row is skipped, not clobbered.)
- Map the CSV columns to the matrix child fields. The headers are fixed by
  `src/sanmar_netsuite/transform/csv_export.py::CSV_COLUMNS`; key ones:
  `External ID`, `Item Name/Number`, `Subitem Of` (the parent link — by internal
  id for numeric styles), `Matrix Attribute 1 - Size`,
  `Matrix Attribute 2 - Color`, `UPC Code`, accounts, and `Vendor 1 …`.
- **Save** the map and note its **script id** (e.g.
  `custimport_bsg_sanmar_child`). Pricing columns are intentionally absent —
  the child importer rejects them; prices land afterward via
  `sanmar-sync reconcile-items` (see NETSUITE_SETUP.md).

### 3. Note the File Cabinet folder id

The RESTlet writes each inline CSV into a folder before importing. Pick (or
create) a folder — e.g. *SuiteScripts / bsg-imports* — and note its **internal
id**.

---

## Wiring the automated import (phase B)

The RESTlet accepts the CSV **inline** (`csv` raw text, or `csvBase64`), so the
pipeline hands over the file it just generated in a single signed REST call — no
separate SOAP upload. Add these repo secrets:

```
NETSUITE_CSVIMPORT_SCRIPT_ID   # the bsg_csv_import RESTlet script id
NETSUITE_CSVIMPORT_DEPLOY_ID   # its deployment id
NETSUITE_CSVIMPORT_FOLDER_ID   # File Cabinet folder to stage CSVs in
NETSUITE_CSVIMPORT_CHILD_MAP   # saved import map id, e.g. custimport_bsg_sanmar_child
```

Submit shape (one job per map):

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

**Ordering caveat (net-new parents):** a child import fails if its parent
doesn't exist. The preview keeps net-new-parent rows *out* of the child CSV for
exactly this reason. When the parent-creation phase is added, its parent import
must finish (poll to `COMPLETE`) **before** those children are submitted — CSV
imports are async/queued, so submitting both at once doesn't guarantee order.

---

## Safety

- The diff/preview never writes to NetSuite.
- The saved map is **Add** mode: create-only, never an update or overwrite.
- Everything stays sandbox-gated — `NETSUITE_ALLOW_PRODUCTION_WRITES=false` in
  every workflow until the production cutover (see PRODUCTION_CUTOVER.md).
- Start with a small batch: import the first few rows of
  `data/sanmar_new_children.csv` by hand through the Import Assistant to confirm
  the map is correct before letting the RESTlet drive the full file.

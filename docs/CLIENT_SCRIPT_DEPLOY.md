# Item form vendor-declutter (hide empty vendor fields)

Hides a vendor's field set on the item form when that vendor has no data for the
item (its marker field is empty). A Gildan 8000 (S&S + SanMar) then shows only
S&S and SanMar fields — no empty Momentec / UA / Champro / etc. clutter. It only
changes what's *displayed*; it never touches stored values.

## A. Deploy the User Event script (~3 min, admin) — USE THIS ONE

> **Why a User Event, not a Client Script.** A Client Script's `pageInit` does
> **not** run when you merely *view* a record — NetSuite renders view pages as
> static HTML and never executes the client script (confirmed live: zero script
> output in the browser console on a viewed item). So the old client-script
> approach (`bsg_item_vendor_display.js`) only ever decluttered create/edit, not
> the view you actually look at. The **User Event** version
> (`bsg_item_vendor_display_ue.js`) runs server-side in `beforeLoad`, which fires
> on **view** and hides the empty fields before the page renders.

1. **Upload the file.** Documents > Files > File Cabinet → SuiteScripts →
   **Add File** → upload `suitescript/bsg_item_vendor_display_ue.js`.
2. **Create the Script record.** Customization > Scripting > Scripts > **New** →
   select the uploaded file → **Create Script Record** → type
   **User Event Script**.
   - Name it e.g. `BSG Item Vendor Display (UE)`. **Save.**
3. **Add a Deployment.** On the Script record → **Deployments** subtab → new row:
   - **Applies To** = `Inventory Part` (add more rows for any other item types
     you use — Assembly, Kit, etc.).
   - **Status** = `Released`.
   - **Log Level** = Debug (so the "fields hidden: N" line shows in the
     execution log while you verify), then **Save**.
4. **Verify.** Open a single-vendor item (e.g. a Gildan 8000) → the Momentec / UA
   / Champro groups should be gone; open a multi-vendor item → both vendors show.
   The execution log (on the Deployment record) shows `type=view; vendors with
   data: X; fields hidden: Y` per load.

The vendor list and field IDs live at the top of the script (`VENDORS`). When a
new vendor's custom fields are added, add a matching entry there.

> The old **Client Script** `bsg_item_vendor_display.js` is kept only as a
> reference/fallback (it can still declutter the *edit* form if you ever want
> that). For the view, deploy the User Event above and you can leave the client
> script undeployed.

## B. Show the new fields on the form

These fields exist and are populated by the nightly syncs — they're just not on
the form layout yet (SuiteTalk can't edit form layouts, so this is manual once):

- **S&S per-warehouse (18):** `custitem_ss_qty_il / ks / ga / tx / nv / oh / pa /
  cn / fo / ma / ds / cc` plus the six added this session — `fl / nj / gd / kc /
  ph / td`.
- **SanMar per-warehouse (9):** `custitem_sanmar_qty_*` (Seattle … Richmond).
- **Momentec (2, new this session):** `custitem_mtec_size_guide` (Size Guide
  link) and `custitem_mtec_instock_guaranteed` (In-Stock Guaranteed checkbox).

Add them once: Customization > Forms > your item form → open the subtab you
created for vendor data (e.g. *Vendor Inventory Levels*) → check these fields to
display them there → **Save**. (The old `*_qty_by_whse` text fields were deleted,
so the per-warehouse columns replace them.)

Once they're on the form, the client script in section A hides them for items
that aren't S&S / SanMar / Momentec, same as the rest of each vendor's group.

## C. Deploy the matrix RESTlet (unblocks auto-create of new items)

Auto-creating brand-new matrix items (parent + color/size children) needs a
SuiteScript RESTlet that SuiteTalk cannot deploy for you. Until this is done,
the feeds still update *existing* items — they just can't create new ones.

1. Upload `suitescript/bsg_sanmar_matrix.js` to the File Cabinet (SuiteScripts).
2. Customization > Scripting > Scripts > **New** → select it → **Create Script
   Record** → type **RESTlet** → note the **Script ID** (internal id).
3. On the Script record add a **Deployment** (status *Released*, log in as the
   integration role) → note the **Deployment ID**.
4. Add two GitHub repo secrets: `NETSUITE_MATRIX_SCRIPT_ID` and
   `NETSUITE_MATRIX_DEPLOY_ID` with those two values.

Tell me once these are set and I'll re-fire the auto-create smoke test.

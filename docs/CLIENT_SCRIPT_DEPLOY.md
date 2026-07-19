# Item form vendor-declutter (hide empty vendor fields)

`suitescript/bsg_item_vendor_display.js` is a **Client Script** that hides a
vendor's field set on the item form when that vendor has no data for the item
(its marker field is empty). A Gildan 8000 (S&S + SanMar) then shows only S&S
and SanMar fields — no empty Momentec / UA / Champro / etc. clutter. It only
changes what's *displayed*; it never touches stored values.

## A. Deploy the client script (~3 min, admin)

1. **Upload the file.** Documents > Files > File Cabinet → SuiteScripts →
   **Add File** → upload `suitescript/bsg_item_vendor_display.js`.
2. **Create the Script record.** Customization > Scripting > Scripts > **New** →
   select the uploaded file → **Create Script Record** → type **Client Script**.
   - Name it e.g. `BSG Item Vendor Display`. **Save.**
3. **Attach it to the item form(s).** Two options:
   - *Per form (recommended):* Customization > Forms > Entry Forms → open the
     item custom form you use (e.g. the one on the screenshots) → **Custom Code**
     subtab → set **Client Script** = `BSG Item Vendor Display`. Save. Repeat for
     each item form that should declutter.
   - *Global:* on the Script record add a **Deployment** with **Applies To** =
     Inventory Item (and any other item types). A form-level attachment is
     cleaner because it targets only the forms you choose.
4. **Verify.** Open a single-vendor item (e.g. a Gildan 8000) → the Momentec/UA
   groups should be gone; open a multi-vendor item → both vendors show.

The vendor list and field IDs live at the top of the script (`VENDORS`). When a
new vendor's custom fields are added, add a matching entry there.

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

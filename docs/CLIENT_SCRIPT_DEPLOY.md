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

## B. Show the per-warehouse columns on the form

The individual warehouse fields (`custitem_ss_qty_pa`, `custitem_ss_qty_cc`, the
SanMar `custitem_sanmar_qty_*`, …) already exist and are populated by the nightly
syncs — they're just not on the form layout yet. Add them once:

- Customization > Forms > your item form → **Screen Fields** / the subtab where
  the "S&S Qty By Warehouse" text field lives (e.g. *Vendor Inventory Levels*) →
  check the `S&S Qty: …` and `SanMar Qty: …` fields to display them there. Save.

Once they're on the form, the client script above hides them for items that
aren't S&S / SanMar, same as the rest of each vendor's group.

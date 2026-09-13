# BSG Inventory Count (Suitelet)

A phone / tablet / desktop page for entering physical counts and posting them
as an **Inventory Adjustment** — the "Physical Inventory Worksheet" report,
minus the paper. Source: `suitescript/bsg_inventory_count_sl.js`.

## What it does

1. **The list.** The page opens on every item at the chosen
   location — NetSuite's current inventory snapshot, the same rows as the
   Physical Inventory Worksheet and the *Custom Current Inventory Snapshot 2*
   report, sorted by item name, 100 per page with a **Load more** button.
   Colour/size children of a matrix item list by colour and then by size in
   the order people say them — X-Small, Small, Small-Tall, Medium, Large,
   X-Large, 2X-Large, 3X-Large (youth, one-size and numeric sizes included) —
   not alphabetically; the exports follow the same order. An
   **In stock / All items** toggle switches between items that have quantity
   **on hand** (positive or negative) and the whole catalog at that location
   (the default, zeros included, matching the report's Show Zeros). Stock that
   is only **on order** has not been received, so it is not in stock and is
   not listed under that filter. Each row shows On hand, Avail, Committed and
   On order; the item name opens the item record in a new tab. Only active inventory items (and
   assemblies) show; matrix *parents* never do, because stock lives on the
   color/size children.
   **Search** narrows the list by style #, display name, sales description,
   UPC or vendor code — any words, in any order (`royale 5` finds "Royale NFHS
   V25 Soccer Ball - Size 5"; `0125666912 white` narrows a style to its white
   children). Clearing the search box brings the full list back.
2. **Count** — the Qty box holds **what is on the shelf**. Key it and press
   **Add** (Enter jumps to the next hit, so a keyboard or barcode scanner flows
   down a shelf). Units pulled for open orders are not on the shelf; they come
   back in by ticking those orders, which add on top of the Qty (see step 3).
   So the shelf NetSuite expects is **Available** (on hand − committed), and
   the total it expects is on hand.
   Tick **Correct** to the right of Add when the shelf holds exactly the
   Available quantity: the item joins the sheet at that number, the row turns
   green like any other count, and untick takes it back off. For an item with
   nothing committed, Available *is* on hand, so Correct is a straight
   zero-delta count and the tag reads `On sheet: 3 · no change`. For an item
   with committed units, ticking Correct alone leaves the line short by that
   quantity and the sheet says so — tick those orders to bring the units in,
   and the total lands back on on-hand with a zero delta.
   Nothing is posted yet either way: every line lands on the **Sheet** tab
   with the current on-hand at the chosen location, an editable count with
   `−`/`+` buttons and its delta. Each line also records **who** keyed or
   ticked it and when — the NetSuite user signed in on that device — shown on
   the row, carried on the sheet export, and written to the adjustment line
   memo, so a doubtful count can be traced to a person. The sheet is saved in
   the browser, per location, so a refresh or a dead battery does not lose the
   count.
   A line whose count equals its on-hand is a *counted, no change* record: it
   proves the item was checked, and submit leaves it off the adjustment. When
   **every** line on the sheet matches, submit says "Nothing to adjust", the
   button reads *Yes, record the count*, and no Inventory Adjustment record is
   created at all.
3. **Open orders** — the units that are yours and still on hand in NetSuite
   but not on the shelf: received (or pulled and packed) for a sales order
   that has not been marked shipped, sitting staged, at the decoration
   facility, or waiting for customer pickup. Instead of printing every open
   order, the **Open orders** tab lists every such line at the location,
   grouped by **sales rep** and, inside each rep, by order (oldest first) with
   customer, date and status, as `0 shipped of 5 · 5 to count`. Each rep
   header totals its orders, lines, units to count and ticks, and collapses
   the whole section; the orders themselves start collapsed and open on a
   click (or **Expand all**), each with its own totals. The filter box
   narrows by order number, customer, sales rep or item and opens what it
   matches. Orders carrying no sales rep group under *No sales rep*, last. Tick a line once its units are found and those units are added to
   the item's count on the sheet (the line is created if needed); untick to
   take them back out, and a line that only existed because of a tick leaves
   the sheet again. An order with several lines has a **Select all** row
   under its number that ticks or unticks every countable line at once.
   A line appears only while it still has **committed** units on an order
   that is itself open (Pending Fulfillment, Partially Fulfilled, Pending
   Billing/Partially Fulfilled — never Billed, Closed or Cancelled).
   Committed quantity is the reliable signal: picking or packing does not
   clear it, shipping does, and a line that was zeroed or returned to the
   vendor has none even when a pick record survives. So backordered lines,
   zeroed lines with a leftover pick record, and anything marked **shipped**
   never appear — they are not in the building or are already off the books. Only inventory items and assemblies (the same types the
   count list shows) appear; decoration, setup and other service or charge
   lines such as PP3C never do. Units pulled or put on layaway (a Picked / Packed
   fulfillment that has not shipped) are still inside the order line's
   committed quantity, so they show on that line as context — `10 to count
   (10 layaway)` — rather than as a second row that could be ticked twice.
   Order numbers link to the **sales order**, never the fulfillment. The same
   list opens per item from the **Committed** figure on either page, and from
   **Open orders** on a sheet line; the sheet line shows "Includes N on open
   orders: …".
   When a line's count is below the quantity committed to open sales orders,
   the sheet warns — that is the "physically gone but never marked shipped"
   case, which should be fixed by shipping the fulfillment, not by adjusting.
4. **Export** — each tab has CSV, Excel and PDF buttons. The Count tab
   exports the whole list as filtered on screen (In stock / All items and any
   search), with Pref. vendor and a blank Count column to fill in by hand;
   the Open orders tab exports every line led by Sales rep (sorted rep, then
   date, like the screen) with a Ticked column; the Sheet tab exports the
   lines you have keyed with their Pref. vendor, on-hand, count and
   adjust-by, plus who counted each line and when. CSV and Excel
   cover the whole list; the PDF stops at `PDF_MAX_ROWS` (2,000) and says so.
   Excel needs `N/compress`; if an account lacks it the page says to use CSV,
   which Excel opens.
5. **Submit** — the page posts the sheet to the Suitelet, which re-reads each
   item's on-hand *at that moment* and creates one Inventory Adjustment whose
   lines bring on-hand to the counted quantity (`Adjust Qty. By = counted −
   on hand`). Items whose count already matches are left off; if nothing
   differs, no adjustment is created at all. The result page links straight to
   the transaction. Sheets over 200 lines post as several adjustments, one after
   another.

The adjustment is created **as the logged-in user** — that is the audit trail
you want on a count — and the transaction memo records who counted and when
(editable on the sheet). Each line's memo reads `Counted 20; on hand 25;
counted by Andrew Murray`, or with ticked orders `Counted 20 (incl. 5 on open
orders: JH-SO625); on hand 25; counted by Andrew Murray`, so an auditor can
see which orders the units belonged to and who took the count.

**Several people at once.** With the shared count set up (see below), every
device's lines save to NetSuite as they are keyed and one administrator posts
the merged result; the same item counted in two places is added together and
flagged for review.

**Serialized, lot-numbered and bin-tracked items** need Inventory Detail, which
this tool does not collect. They are flagged in search results (no count box)
and, if they somehow reach a submit, refused server-side with a reason so the
rest of the sheet still posts. Adjust those the normal way.

## Deploy (~5 min, admin — sandbox first)

1. **Upload the file.** Documents > Files > File Cabinet → SuiteScripts →
   **Add File** → `suitescript/bsg_inventory_count_sl.js`.
2. **Create the Script record.** Customization > Scripting > Scripts > **New**
   → select the uploaded file → **Create Script Record** → type **Suitelet**.
   Name it `BSG Inventory Count`. **Save.**
3. **Add a Deployment.** On the Script record → **Deployments** subtab → new
   row:
   - **Status** = `Released`, **Log Level** = `Audit` (each submit logs one
     `invcount: adjustment <id>` line with the counts).
   - **Audience** → the roles that count stock (Administrator, Warehouse, …).
   - Leave **Execute as Role** blank so the adjustment posts as the person
     counting. Their role needs **Items**, **Locations** and **Accounts**
     (View); a role that will *submit* also needs **Inventory Adjustment
     (Create)**.
   - Leave **Available Without Login** *unchecked*.
   - **Save** and open the deployment's **URL** — that link is the app.
4. **Put it where people will find it.** Add the URL as a Center Link
   (Customization > Centers and Tabs > Center Links) and/or a dashboard
   shortcut. On a phone, open the link in Safari/Chrome and use **Add to Home
   Screen** — it runs full-screen like an app.

## Configuration (top of the script, `CONFIG`)

| Key | Default | Meaning |
|-----|---------|---------|
| `ADJUSTMENT_ACCOUNT_ID` | `222` | Internal id of the account the adjustment posts against (the header **Account** field). Locked to **5005 INVENTORY ADJUSTMENT** (Cost of Goods Sold), internal id 222 in the production account. A sandbox refreshed from production carries the same id; if a submit complains about the account, check Lists > Accounting > Accounts (Internal ID column). Set to `null` and the sheet instead shows a dropdown of active Expense / COGS / Other Expense accounts, pre-selects one whose name mentions "adjust", and remembers the pick per browser. |
| `SUBSIDIARY_ID` | `null` | OneWorld: force the subsidiary. `null` = the chosen location's subsidiary, else the logged-in user's. |
| `ITEM_TYPES` | `['InvtPart', 'Assembly']` | Item types that can be counted. |
| `SEARCH_PAGE_SIZE` | `100` | Rows per page of the list (a **Load more** button pages on). |
| `IN_STOCK_DEFAULT` | `false` | `false` opens on every item, zeros included (like the *Custom Current Inventory Snapshot 2* report with Show Zeros on); `true` opens on items with quantity on hand (on-order-only items are not in stock). The toggle on the page overrides it and is remembered per browser. |
| `MAX_LINES_PER_ADJUSTMENT` | `200` | Bigger sheets post as several adjustments. |
| `SHARED_RECORD` | `customrecord_bsg_count_line` | The custom record that holds every device's count lines (see *Shared count*). `null` switches the shared count off: one sheet per device, submitted from that device. |
| `SHARED_FIELDS` | `custrecord_bcl_*` | The record's field IDs, if you named them differently. |
| `SHARED_BATCH` | `100` | Items per Inventory Adjustment when posting the shared count (each line is also marked posted in the same request). |
| `SYNC_MAX_LINES` | `50` | Lines a device sends per save; a burst is sent in several saves. |
| `MEMO_PREFIX` | `Physical count` | Default memo when the counter leaves it blank: `Physical count 2026-09-11 - Andrew Murray`. |
| `SUBMIT_ROLE_IDS` | `[3]` | Internal ids of the roles allowed to post the adjustment. `3` is NetSuite's Administrator. Every other role counts, ticks open orders and exports, but sees no **Refresh on-hand** / **Clear sheet** / **Submit count** buttons, and the submit endpoint refuses them — a hidden button is not a permission. Find a role's id in the `id=` of its URL under Setup > Users/Roles > Manage Roles. `null` lets any role in the deployment's audience submit. |
| `EXPORT_MAX_ROWS` | `20000` | Cap on rows in a CSV / Excel export. |
| `PDF_MAX_ROWS` | `2000` | Cap on rows in a PDF export (the renderer is slow on large tables). |

**Locations.** With *Multi-Location Inventory* on (it is), the header has a
location picker: on-hand quantities are per location and every adjustment line
carries it. The picker is remembered per browser and defaults automatically
when the account has a single active location. Without the feature the picker
disappears and account-wide on-hand is used.

## How to run a count

1. Open the link on whatever device is handy; pick the **location** once.
   The in-stock list loads on its own.
2. Walk the list (or search to jump), key a count, **Add**. Repeat. Counts of
   **0** are valid (they zero the item out). Items you never key a count for
   are left exactly as they are — the tool never zeroes anything on its own. Switch to the **Sheet** tab any time to review or fix lines
   (`×` removes one, **Clear** removes all of yours — nothing is posted until
   Submit).
3. Open the **Open orders** tab and walk the staging area, the decorator log
   and the pickup rack: tick each line as its units are found. Use the filter
   box to jump to a customer, order number or item.
4. If a count spans hours, **Refresh on-hand** on the sheet re-reads the current
   quantities so the deltas stay honest (submit re-reads them again anyway).
6. **Submit count…** → confirm → the adjustment link appears. Any lines the
   server refused stay on the sheet with the reason. The adjustment posts to
   5005 INVENTORY ADJUSTMENT; there is nothing to pick.

**Counters who cannot submit.** Only `SUBMIT_ROLE_IDS` (Administrator by
default) sees Refresh on-hand, Discard and Submit count. Everyone else counts
and ticks orders exactly the same way. With the **shared count** set up (next
section) their lines save to NetSuite as they go and appear on the
administrator's sheet — nothing to hand over. Without it, their sheet lives
only in their own browser: they **export** it (CSV, Excel or PDF) and an
administrator keys the counts.

## Shared count (several people, one submit)

Five people counting on five devices is the normal case, and one administrator
should post the result once. The shared count does that: every line a device
keys, ticks or edits is saved to NetSuite within a second or two (a status in
the page header reads *Saved to NetSuite*, *Saving…*, or *Not saved — retrying*),
and the administrator's **Sheet** tab shows everyone's lines merged per item.

**One-time setup (~8 minutes, administrator).** Create a custom record type
under Customization › Lists, Records & Fields › Record Types › **New**:

| | Value |
|---|---|
| Record type ID | `customrecord_bsg_count_line` (name it *BSG Count Line*) |
| Access Type | **No Permission Required** — counters' roles write to it through the page |
| Include Name Field | Either way — the page fills a Name in if the field exists |
| Field `custrecord_bcl_item` | List/Record → **Item**, mandatory |
| Field `custrecord_bcl_location` | List/Record → **Location** |
| Field `custrecord_bcl_shelf` | Decimal Number |
| Field `custrecord_bcl_orders` | Long Text |
| Field `custrecord_bcl_onhand` | Decimal Number |
| Field `custrecord_bcl_counter` | List/Record → **Employee** |
| Field `custrecord_bcl_device` | Free-Form Text |
| Field `custrecord_bcl_device_label` | Free-Form Text |
| Field `custrecord_bcl_posted` | Check Box |
| Field `custrecord_bcl_adjustment` | List/Record → **Transaction** |

Type the IDs exactly (NetSuite prefixes `custrecord_` for you on the field
form — enter the part after it if the prefix is already shown). Until the
record exists, administrators see a **Shared count is not set up yet** notice
on the Sheet tab listing anything missing, and the page keeps working the old
way — one sheet per device.

**How it behaves.**

- Each device names itself once on the Sheet tab (*This device* →
  "Warehouse tablet 1"). The administrator sees `Jeff Howard · Warehouse
  tablet 1 · 60 · 10:02` under each item, so a count traces to a place as well
  as a person.
- **The same item counted by two people shows as two sub-lines and is added
  together** — a warehouse count of 60 and a retail-floor count of 30 post as
  90. The row is flagged *2 counters* and listed under **Needs review**. Untick
  a sub-line to leave it out (a true duplicate rather than a second area), or
  type a total by hand; a hand-set total is marked and can be reset.
- Open-order ticks merge per order: two people ticking `JH-SO625` count its
  units once.
- The administrator's deltas are against **fresh** on-hand. A sub-line whose
  on-hand has moved since it was counted says so (*on hand was 100 when
  counted*), and the item is flagged.
- **Submit** posts one adjustment per 100 items from the merged totals, memo
  `Counted 90 (…); on hand 100; counted by Jeff Howard, Amy Fox`, and marks
  every line behind it posted. Within a minute (or as soon as they return to
  the tab) the counters' own sheets clear those lines.
- If a counter changes a line after the administrator loaded the sheet, the
  submit is refused — *New counts arrived for N items* — and the sheet
  reloads with the new numbers; the administrator's decision on that item is
  forgotten, the others kept. New items simply appear on the next load.
- Before the confirm, the page checks for items a count already adjusted
  **today** and lists them, so a second adjustment of the same item is a choice.
- **Discard all counts…** deletes every open line at the location; each
  device's sheet empties on its next reconcile. A counter can **Clear my lines
  on this device** at any time.
- A wiped browser (cleared data, private mode ended) gets its lines back from
  NetSuite on the next load, under the same device id.

Posted lines stay in NetSuite (Lists › Custom › BSG Count Line) linked to the
adjustment, so who counted what is searchable later.

## Count-day rules (what moves inventory)

- A sales order commits stock but does not move it: On hand stays, Available
  drops. A purchase-order item receipt raises On hand. **Marking an item
  fulfillment Shipped** is what lowers On hand (Picked / Packed do not, with
  Pick, Pack, Ship on). **Invoicing a sales order does nothing to inventory**;
  only a stand-alone invoice or cash sale with no order behind it relieves
  stock when saved.
- Before counting, mark Shipped every fulfillment that has physically left,
  then hold shipping until the count is submitted.
- Count staged orders that are still in the building and not marked Shipped
  (tick them on the Open orders tab). Do not count anything already marked
  Shipped, even if the box is still on the dock.
- Units at the decoration facility are still on hand: tick their orders from
  the decoration log.

Several people count at once from their own devices. With the shared count
set up, one administrator posts everyone's lines together and two counts of
the same item are added — so split the work by **item** (vendor, item-number
range) rather than by area where you can, and leave *Correct* for items you
have seen all of. Without the shared count, each device's submit is its own
adjustment and would overwrite another's, so only one person should submit.

## Verify in sandbox

Deploy to the sandbox account first (the repo's usual rule). Count two or three
items with deliberate differences, submit, and open the resulting Inventory
Adjustment: the **Adjust Qty. By** column should be `counted − on hand` per
line, the location and account as chosen, and the item's on-hand afterwards
should equal what was keyed. Then repeat the same count — it should report
"No adjustment needed".

## Troubleshooting

- **"Please enter value(s) for: …"** on submit — a mandatory field NetSuite
  wants on Inventory Adjustments (often Department/Class under accounting
  preferences, or Est. Unit Cost on a positive adjustment for an item with no
  cost yet). The message is shown as-is on the sheet; the adjustment was not
  created. Fix the preference or the item and submit again.
- **"You do not have permission…"** — the counter's role lacks Inventory
  Adjustment (Create) or view access on Items / Locations / Accounts.
- **An item is missing from the list** — with **In stock** on, it has nothing
  on hand at this location (on order does not count until it is received):
  switch to **All items**.
  Otherwise it is inactive, a matrix parent (count its children), a
  non-inventory item, or not set up at the chosen location.
- **"This account does not support the In stock filter"** — NetSuite rejected
  every quantity filter field, so the list falls back to all items. The
  execution log names the rejected fields (`invcount: this account rejects
  item filter …`); those are remembered for a day so pages stay fast.
- **Non-JSON response** errors on the page mean NetSuite returned an HTML error
  page; the deployment's execution log has the stack.
- **"Shared count is not set up yet"** (administrators, Sheet tab) — the custom
  record or one of its fields is missing; the notice names what. Counters see
  nothing and keep counting; their sheets just stay on their devices until it
  exists.
- **"Not saved — retrying"** in the header — the device cannot reach NetSuite
  (session expired, no signal). Counts stay on the device and go up when it
  can; do not close the tab while it says so (the browser warns).
- **"New counts arrived for N items"** on submit — a counter changed one of
  those items after the sheet loaded. Nothing posted; the sheet has reloaded
  with their numbers. Look at the flagged items and submit again.
- **Lines reappear after a submit** — the adjustment posted but marking those
  lines took more than one request and the last one failed (the result page
  says so). Open the adjustment; if those items are on it, use **Discard**
  for the leftovers rather than submitting them again.
- **The page looks right but the type is Helvetica/Arial** — the page asks
  Google Fonts for Archivo. If the warehouse network blocks
  `fonts.googleapis.com` it falls back to the system sans automatically;
  nothing else changes and no error is shown.

## Updating the script

Edit `suitescript/bsg_inventory_count_sl.js`, then either re-upload it over
the same File Cabinet file (Edit → Select File) or push it with the
**NetSuite File Cabinet Push** workflow (`src_dir` = `suitescript`, `script_id`
= this Suitelet's script id). No redeploy is needed — the page never caches
itself.

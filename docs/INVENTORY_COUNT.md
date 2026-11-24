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
   children). A word with a digit in it is treated as a style number, UPC,
   vendor code or size and matches where a code, or one of its dash / slash /
   colon parts, **begins** — never the middle: `126` lists 126, 126300 and
   `126 : 126-Black` but never 0126; `1379806-black` finds that style's Black
   children; `5` still finds the size-5 balls (`…-5`) and anything with "5"
   in its description. Plain words match anywhere. Clearing the search box
   brings the full list back.
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
   With the shared count on, the list also shows what **other devices** have
   counted: an item someone else keyed goes green with *Counted by Steve
   Eversoll · Retail iPad: 195 · 9/14 11:48 am* (and *Also counted by …* under
   your own tag when you both keyed it), and if their shelf count is the
   expected quantity their **Correct** shows ticked and locked. That is
   information, not a lock on the row: keying it again adds a second line the
   administrator sees side by side and reviews. It refreshes when the list
   paints, every 30 seconds while the Count or Open orders tab is showing,
   and when the tab comes back into view.
   Nothing is posted yet either way: every line lands on the **Sheet** tab
   with the current on-hand at the chosen location, an editable count with
   `−`/`+` buttons and its delta. Each line also records **who** keyed or
   ticked it and when — the NetSuite user signed in on that device — shown on
   the row, carried on the sheet export, and written to the adjustment line
   memo, so a doubtful count can be traced to a person. The sheet is saved in
   the browser, per location, so a refresh or a dead battery does not lose the
   count.
   The sheet's summary totals the **units up** and **units down**, and under
   each is what those units are **worth at average cost** — this location's
   average cost for the item where NetSuite keeps one per location, the
   item's own otherwise. It is the value of the adjustment about to be
   posted, not of the inventory. An account that does not report an average
   cost shows the unit totals alone rather than a misleading `$0.00`.
   **Your place in the list is kept.** The page rebuilds itself as counts
   save and as the shared count refreshes, and a reload starts it empty and
   fills it from NetSuite a moment later; either would otherwise drop
   whoever was halfway down Needs review back at the top. The place is
   remembered by the row that was at the top of the screen, so it holds even
   while rows above it arrive, change height as on-hand lands, or leave
   because someone else's count came in. Each tab keeps its own place, it
   survives a manual reload, and it is per browser tab — two tabs do not
   fight over it, and closing the tab forgets it. Emptying the page does not
   reliably leave it at the top — the browser holds the scroll at the bottom
   of the short *Loading everyone's counts…* page instead — so neither the
   saving nor the putting-back goes by "is it at the top": a page too short
   to hold the place is known to be mid-rebuild and leaves it alone, and the
   place is put back unless the reader is already on it. It lands on the row
   they were reading rather than an exact pixel, which drifts by a fraction
   of a row as on-hand values arrive and rows change height.
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
   customer, the sales order's own **PO #** and header **memo** (the order's
   Memo field, not a line's; right after the customer name, when either is
   filled in), date and status, as
   `0 shipped of 5 · 5 to count`. Each rep header totals its orders, lines,
   units to count and ticks, and collapses the whole section; the orders
   themselves start collapsed and open on a click (or **Expand all**), each
   with its own totals. **Collapse all** folds everything all the way down to
   the sales rep names (not just each order's lines); **Expand all** reopens
   every rep section and every order under it. **Select all**, next to them,
   ticks every countable line for every sales rep and every order at once
   (someone else's already-ticked lines are left alone); **Clear my ticks**
   undoes every line ticked from this device the same way. Both ask for
   confirmation first, naming how many lines are about to move, since one
   click can affect thousands of lines across the whole location. Type in
   the filter box first to narrow either button to just what it matches —
   an item, a sales rep, a customer or an order number — instead of
   everything at the location. The filter box narrows by
   order number, customer, sales rep or item and opens what it matches, even
   inside a rep section that was collapsed. Orders carrying no sales rep
   group under *No sales rep*, last. Tick a line once its units are found and
   those units are added to
   the item's count on the sheet (the line is created if needed); untick to
   take them back out, and a line that only existed because of a tick leaves
   the sheet again. An order with several lines also has its own **Select
   all** row under its number that ticks or unticks just that order's
   countable lines at once.
   **Two lines of the same item on one order** — two decoration jobs on one
   blank, say — are told apart by the line's own position on the order, not
   just the order number, so each is listed and tickable on its own and both
   contribute their own quantity; ticking one never shows the other as
   already done, and unticking one only takes its own units back out. An
   order that has only **one** line for an item is not touched by any of
   this — it keeps the plain order number as its identity, exactly as
   always, so a tick made before this feature shipped still matches and
   locks it correctly.
   **An order line's units are counted once however many ticks name it.**
   A line is matched by the order it belongs to, and only split in two
   when the ticks on it carry genuinely different line numbers. So a tick
   stored before this feature existed and one stored after it are
   recognised as the same line, not two; ticking a line that already
   carries an older tick replaces it rather than stacking on top of it;
   and the line's total is rebuilt from the shelf count plus its ticks
   each time, so nothing can stay baked into a total after its tick is
   gone. (While this feature's keying was being settled, the identity of
   a tick changed twice in a day. Ticks stored under the earlier scheme
   stopped matching, so lines showed as untouched, were ticked again, and
   their units were added twice. v2026-09-15.1 made every stage of this
   tolerant of the older labels, which is what stops it recurring.)
   A line appears while it still has **committed** units on an order that is
   itself open (Pending Fulfillment, Partially Fulfilled, Pending
   Billing/Partially Fulfilled — never Billed, Closed or Cancelled), **or**
   while it has units **pulled** for it with nothing committed — a line
   marked **Do Not Commit**, or an ordinary line whose committed quantity
   has already moved into a pending Item Fulfillment once it was picked
   (BSG sees both). Either way the pulled quantity is the only sign those
   units are standing in the building; such a line is listed on what was
   pulled, capped at what the order still has open, and says so —
   `0 shipped of 40 · 40 to count (40 pulled, do not commit)`. Pick, Pack,
   Ship's three stages (BSG labels them Pulled, Layaway and **Shipped**) are
   all fetched — the third stage's own "Shipped" label describes a
   warehouse step, not proof the units left, which is checked separately
   from the sales order's own ship/receive quantity; a line found only
   there reads `(40 packed, not yet shipped)` so nobody mistakes it for
   gone.
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
4. **Export** — each tab has CSV and PDF buttons, plus Excel where the account
   has NetSuite's `N/compress` module (BSG's does not, so the button is not
   shown there; CSV opens in Excel). The Count tab
   exports the whole list as filtered on screen (In stock / All items and any
   search), with Pref. vendor and a blank Count column to fill in by hand;
   the Open orders tab exports every line led by Sales rep (sorted rep, then
   date, like the screen) with the order's PO # and memo columns right after
   Customer and a Ticked column; the Sheet tab exports Item, Description,
   **Color**, **Size**, Pref. vendor, On hand, Count, Adjust by, **Avg
   cost**, **Adjust $**, the open orders ticked into the line, and who
   counted it and when.
   **The Sheet export follows the tab it is exporting.** On **Needs review**
   it carries only the items being reviewed; on **All** it carries every
   line. The file says which it is, and what the whole thing nets at average
   cost.
   Color and size come from the item's own name, split off using the matrix
   parent NetSuite records for it — `1379806 : 1379806-Black-2X-Large` is
   Black / 2X-Large. An item with no matrix parent has neither to report and
   both are left blank rather than guessed at from whatever follows a dash
   in its code. **Adjust $** is the adjust-by quantity at that item's
   average cost, signed the same way, so a short count reads negative; both
   money columns are left blank for an item with no average cost. They are
   plain numbers, not `$` text, so a spreadsheet can total them.
   **Every export ends with a grand total row** for the columns a sum means
   something in: On hand / Available / Committed / On order on the Count
   tab, Ordered / Shipped / To count / Pulled on Open orders, and On hand /
   Count / Adjust by / Adjust $ on the Sheet. **Avg cost has no total** — a
   column of averages added together is not the value of anything — and
   neither does the Count tab's blank fill-in-by-hand column. The row is
   labelled `TOTAL (157 lines)` in the first column, and in Excel it is a
   real number, so a formula can use it.
   The **Excel** file opens ready to work with: the header row carries the
   filter buttons and stays frozen while the list scrolls. The filter range
   stops at the last real row, so the grand total underneath can never be
   sorted into the middle of the list or hidden by a filter. **CSV cannot
   carry a filter** — it is plain text, with no place to keep one — so in
   CSV the header is just the first line; Excel's own Ctrl+Shift+L adds a
   filter to it in one keystroke. CSV and Excel
   cover the whole list; the PDF stops at `PDF_MAX_ROWS` (2,000) and says so.
   Excel needs `N/compress`; if an account lacks it the page says to use CSV,
   which Excel opens.
5. **Submit** — the page posts the sheet to the Suitelet, which re-reads each
   item's on-hand *at that moment* and creates **one** Inventory Adjustment whose
   lines bring on-hand to the counted quantity (`Adjust Qty. By = counted −
   on hand`). Items whose count already matches are left off; if nothing
   differs, no adjustment is created at all. The result page links straight to
   the transaction. Without the shared count, a device's own sheet of more than
   200 lines posts as several adjustments, one after another; the shared count
   always posts one (see *Shared count*).

The adjustment is created **as the logged-in user** — that is the audit trail
you want on a count — and the transaction memo records who counted and when
(editable on the sheet). Each line's memo reads `Counted 20; on hand 25;
counted by Andrew Murray`, or with ticked orders `Counted 20 (incl. 5 on open
orders: JH-SO625); on hand 25; counted by Andrew Murray`, so an auditor can
see which orders the units belonged to and who took the count.

**Several people at once.** With the shared count set up (see below), every
device's lines save to NetSuite as they are keyed and one administrator posts
the merged result; the same item counted by two people is flagged for review
and, unless both found the same number, added together (or the latest kept,
at the administrator's choice).

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
| `MAX_LINES_PER_ADJUSTMENT` | `200` | Without the shared count, a device's sheet bigger than this posts as several adjustments. |
| `SHARED_RECORD` | `customrecord_bsg_count_line` | The custom record that holds every device's count lines (see *Shared count*). `null` switches the shared count off: one sheet per device, submitted from that device. |
| `SHARED_FIELDS` | `custrecord_bcl_*` | The record's field IDs, if you named them differently. |
| `SHARED_COUNTED_AT` | `custrecord_bcl_counted_at` | Optional field on the shared record that holds when each line was last *counted* (see *Keep the latest*). The page works without it; `null` never looks for it. |
| `SHARED_BATCH` | `100` | Items sent per request when posting the shared count. Every batch is added to the **same** Inventory Adjustment, and each line is marked posted in the same request. |
| `FULL_COUNT_RECENT_DAYS` | `7` | Full year-end count: an item with no open count line still counts as counted if one of its lines was posted within this many days, so a second submit never resets what the first one posted. |
| `ORDERS_MAX_ROWS` | `20000` | Open orders: sales-order lines read before narrowing to what is countable. |
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
   **0** are valid (they zero the item out). In a **spot count**, items you
   never key a count for are left exactly as they are. In a **full year-end
   count** they are reset to 0 after the administrator reviews the list (see
   *Spot count or full year-end count*). Switch to the **Sheet** tab any time to review or fix lines
   (`×` removes one, **Clear** removes all of yours — nothing is posted until
   Submit).
3. Open the **Open orders** tab and walk the staging area, the decorator log
   and the pickup rack: tick each line as its units are found. Use the filter
   box to jump to a customer, order number or item.
4. If a count spans hours, **Refresh on-hand** on the sheet re-reads the current
   quantities so the deltas stay honest (submit re-reads them again anyway).
5. **Submit count…** → confirm → the adjustment link appears. Any lines the
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
| Field `custrecord_bcl_counted_at` | Free-Form Text — **optional**, see *Keep the latest* below |

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
- An administrator's **leave-out** and **hand-set-total** decisions on the
  merged sheet are remembered in this browser and survive a real page
  reload, not just a same-tab re-fetch, so an unticked duplicate does not
  quietly come back.
- **The same item counted by two people shows as two sub-lines.** If both
  found the **same number**, that is one count, not double — they found the
  same shelf — and the row says *same count, counted once*. If the numbers
  **differ**, the sheet's *Two people counted the same item* switch decides:
  **Add them up** (the default: a warehouse count of 60 and a retail-floor
  count of 30 post as 90, *added together*) or **Keep the latest** (the same
  shelf counted again, so the newest count wins, *latest kept (Jeff
  Howard)*). The notice above the list says how many items two people
  counted, how many agree, and how many of the units up / down sit on those
  items — the first place to look when a total looks too high. **The switch
  applies to every such item alike, whether or not the two numbers happen to
  match** — landing on the same number is not proof of the same shelf, so
  under Add them up it is still added (a genuine coincidence adds up like
  any other pair); it is Keep the latest that is for a shelf counted twice
  by mistake. The row is flagged *2 counters*; untick a sub-line to leave it
  out, or type a total by hand (marked, resettable). The choice is
  remembered in this browser, including across a reload, so it decides what
  a later submit actually posts. **Needs review** lists every item that
  would actually post a + or − adjustment — one counter or several, on hand
  moved or not — plus anything that cannot post normally (missing or
  blocked); an item whose total lands exactly on its on-hand needs nobody's
  time and stays under **All**.
- A **hand-set total belongs to the counts it was typed over.** If a counter
  re-keys that item, a second person counts it, or its lines are cleared and
  it is counted afresh later, the hand-set total is dropped, the counted
  total comes back, and a notice on the sheet names the items so the
  administrator can set it again if it still applies. (Until v2026-11-24.1
  the old number stayed in the browser by item and went on deciding what
  posted.)
- One item can carry up to 500 ticked order lines. (Until v2026-11-24.1 the
  server kept the first 30 and silently dropped the rest, so a popular blank
  on more than 30 open orders — easy to reach with **Select all** — saved
  short by those units while the counter's own screen still showed them.)
- A count never flickers off a counter's sheet. The once-a-minute check
  against NetSuite used to compare the sheet with an answer that could be a
  second or two older than the last save, so a line saved in that gap was
  taken for "posted or discarded" and vanished until the next check, and a
  line just removed came back the same way. Since v2026-11-24.1 a save or a
  delete newer than the answer wins.
- **Keep the latest goes by when an item was counted, once the record has
  the `custrecord_bcl_counted_at` field.** Without that field the page can
  only go by when a line was last *saved*, and a save is not a count:
  renaming a device re-saves every one of its lines, so an older count can
  look newest and win. With the field (Free-Form Text, added to the BSG
  Count Line record the same way as the others — nothing else to do, the
  page notices it within five minutes), the server stamps a line only when
  its shelf number or its ticked orders actually change, and the times shown
  beside each counter are count times too. Lines counted before the field
  existed have no stamp: they rank behind stamped ones and keep going by
  their save among themselves. While the field is missing and Keep the
  latest is switched on, the notice above the list says so. Add them up, the
  default, never depended on any of this.
- Every device sees everyone's open-order ticks. A line someone else ticked
  shows checked and locked with *ticked by Jeff Howard · Warehouse tablet 1*,
  the order and rep headers count it, and **Select all** skips it; the view
  refreshes each minute and on Reload. Only the device that ticked a line can
  untick it (the administrator can leave it out on the merged sheet). **An
  order line's units are never counted twice, even by accident**: if two
  people tick the very same line within the same refresh window (before
  either has seen the other's tick), the merged sheet still adds that line's
  units in only once — it is matched by the line's own identity, not just
  summed like two people's separate counts of the same item. The header's
  **Select all** re-checks who has already ticked what right before it grabs
  a batch, since one click there can claim far more lines than clicking them
  one at a time.
- The administrator's deltas are against **fresh** on-hand. A sub-line whose
  on-hand has moved since it was counted says so (*on hand was 100 when
  counted*), and the item is flagged.
- **Submit** posts **one** adjustment from the merged totals, however many
  items the count covers. A big count is sent to the server in batches of
  `SHARED_BATCH` (100) so no single request is huge, but only the first batch
  creates the record — every batch after it is added to that same adjustment,
  which it is told the id of. (Until v2026-09-15.7 each batch made its own
  record, so a 2,310-item count posted 24 adjustments instead of one.) Memo
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

## Spot count or full year-end count

The administrator's Sheet tab has a switch above the list that decides what
a submit covers. It always opens on **Spot count** and is never remembered,
so a full count is chosen on purpose, every time.

- **Spot count** — only the items counted change. Everything else at the
  location is left exactly as it is. Use it for a cycle count, a recount of
  one vendor, or fixing a handful of items mid-year.
- **Full year-end count** — items counted or ticked post as counted, and
  **every other item with stock on the books at the location is reset to 0**
  in the same adjustment. In a wall-to-wall count, an item nobody found is
  not in the building.

"Not counted" and "not there" look identical to the system, so nothing is
reset blind. Pressing **Submit full year-end count…** lists, fresh, every
active item with a non-zero on-hand that has no count line, and the confirm
shows:

- how many items will be reset, with the units and their value at average
  cost; **View** shows the list and **Export (CSV)** saves every row;
- items that need inventory detail (serial, lot, bin): listed, **not**
  reset — adjust those the normal way;
- items with no open count that were counted and posted in the last
  `FULL_COUNT_RECENT_DAYS` (7) days: left as posted.

The post button stays disabled until the list has loaded whole and the
administrator ticks **I have reviewed the list**. If a shelf, a back room or
an outside location was skipped, its stock is on that list: cancel and count
it first.

What a full count does and does not do:

- A reset line's memo reads `Not counted in full count; on hand 12; reset to
  0`, so it can be told from a counted zero later. Negative on-hand is
  brought up to 0 the same way.
- Counted items post first and resets after, so a count that fails part-way
  has recorded what was found before anything is written off.
- It is safe to submit again. After a failed batch, or when part of the
  count was posted earlier in the week, the items already posted have no
  open line any more; the 7-day rule is what stops them being taken for
  "never counted" and zeroed. The server checks every reset line again when
  it arrives: an item someone counted after the list was reviewed makes the
  submit stale (nothing posts, the sheet reloads), and one posted in the
  last 7 days is refused with a reason.
- **One location per submit.** Each location is its own full count.
- **Inactive items are never touched**, and neither are items that are not
  inventory items or assemblies.
- An item that is on the sheet but has every sub-line left out stays as it
  is — set its total by hand to 0 if that is what it should be.
- If there are more items or count lines than can be listed whole (20,000
  items in stock, 5,000 count lines), the full count refuses to post: a
  partial reset would be worse than none.
- It needs the shared count (the custom record). Without it there is only
  the per-device spot count.
- The adjustment is dated the day it is submitted. For it to land in the
  closing year, submit on or before the last day of that year, with
  shipping and receiving held from the count until the submit.

## Keeping a count moving (v2026-11-24.4)

Four things that could quietly stall or shorten a count, found on a second
review with the September test run in mind:

- **A dropped connection no longer freezes a device.** A request that never
  got an answer (wifi dropped, tablet slept) used to leave that device on
  *Saving to NetSuite…* until someone reloaded the page, with nothing
  reaching NetSuite in the meantime. It now reads *Not saved — retrying:
  Could not reach NetSuite* and goes up by itself when the connection
  returns.
- **Open orders are not cut off at 5,000 lines.** The tab read at most 5,000
  sales-order lines, oldest first, before narrowing them to what is
  countable. BSG had 5,139 in November 2026, so the newest orders were
  missing and their staged units could not be ticked. The limit is now
  `ORDERS_MAX_ROWS` (20,000), and if it is ever reached the tab says so in
  words.
- **One line NetSuite refuses no longer strands the count.** A refused line
  failed its whole batch of 100, on every retry. The page now halves a
  refused batch until the line stands alone, sets it aside on the result
  page with NetSuite's own reason, and posts everything else. If the
  adjustment itself has grown too slow to add to, the same batch is sent
  once more as a new adjustment and the count carries on into that one, so a
  very large count may finish as two or three adjustments rather than fail.
  Stale counts, a lost session and a lost connection still stop at once.
- **Not counted yet…** (administrator, Sheet tab) lists, at any point in the
  count, the items with stock on the books that nobody has counted, with a
  count per preferred vendor and a CSV. It is the same list a full count
  resets, asked for early enough to send someone to the shelf. The full
  count's review also flags uncounted items that have units **committed to
  open orders nobody ticked** — usually staged or at the decorator.

## Who is counting, and who is finished (v2026-11-24.6)

Built for the problem the September test run showed most clearly: people
counting in different rooms, nobody telling the administrator where they
were, and one device that looked fine and had saved nothing.

- **Device board** (administrator, Sheet tab). Every device that has the
  page open reports in about once a minute: whose it is, how many lines it
  holds, **how many have not saved** and why, and whether its counter has
  finished. The board lists them all — including a device that has never
  managed to save a line, which the per-counter chips cannot show. A device
  not heard from for three minutes says so (screen off, tab closed, no
  signal), and one still running an older copy of the page is told to
  reload. The board is a status display kept for a day, not a record.
- **I'm finished counting** (every counter, Sheet tab). Pressed when a
  counter has done everything they were given. It is refused while any of
  their lines is still unsaved. Counting or ticking anything afterwards
  takes the mark off again, and it clears by itself once the count is
  posted. It is per location.
- **The confirm names who it is waiting on.** Before a submit, any device
  that has not marked itself finished, or still has unsaved lines, is listed
  — and for a full year-end count the notice says those unsaved items would
  be reset to 0. It is a warning, not a lock: a tablet that was simply left
  open does not hold the count hostage.
- **Whole-location Select all is the administrator's.** The header **Select
  all** on Open orders marks every committed unit on every open order as
  found, in one click. Counters no longer have it; they keep **Select all**
  inside each order and **Clear my ticks**.
- **Open orders reads only the open orders' fulfillments** (v2026-11-24.5).
  The pulled / layaway search used to ask for every fulfillment ever made,
  oldest first, so its 5,000-row limit was spent on years-old shipments
  before reaching today's orders. Units that have really shipped are now
  netted out of *pulled*.

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
- **The Open orders tab is grouped by the order's Sales Rep field, not by who
  is counting.** A bucket named after an employee (e.g. "Jeff Howard") is
  every open order where that person is the assigned sales rep — it has
  nothing to do with which device ticked those lines. Someone counting the
  floor will tick lines under many other people's names; do not read a
  bucket's tick count as "how much this person has counted."
- **Someone's ticks or counts are not showing on another device** — first
  compare the version in each page's footer (`v2026-09-15.7` …): a tab left
  open from before an upload keeps running the old copy until it is reloaded.
  Then open the Open orders tab (it fetches every device's ticks each time it
  opens, on Reload, and every 30 seconds while showing). If NetSuite refused
  the request, the tab says *Other devices' ticks could not be loaded* with
  the reason. To check whether a specific device's data is reaching NetSuite
  at all (regardless of what the Open orders tab shows), an administrator can
  open the Sheet tab: the row of chips under the summary numbers lists every
  counter and device that has actually saved a line, with a count and a last
  update time. No chip for that person means nothing from their device has
  reached NetSuite yet — the problem is on their end (see the next item), not
  in how ticks are displayed.
- **"Shared count is not set up yet"** (administrators, Sheet tab) — the custom
  record or one of its fields is missing; the notice names what. Counters see
  nothing and keep counting; their sheets just stay on their devices until it
  exists.
- **"No signed-in user: NetSuite reports user id "-5" …"** with a red
  *NetSuite is not reporting a signed-in user for this tab* banner — the
  signed-in user's id is not a positive number. Up to 2026-09-14.6 the page
  treated that as "no user" and refused every save from that device, so its
  lines stayed in the browser. But a negative id can be a real employee:
  NetSuite numbers the account's **original administrator** employee **-5**
  (Jeff, at BSG — he is listed at internal id -5 on Lists → Employees), and
  an internal id can never be changed. Since 2026-09-14.7 any id but 0 and
  -4 (an anonymous request) is checked against the active employee list and
  used as is; a login that is no employee at all is matched to one by e-mail
  address, then by name. The answer is remembered for a day. If the banner
  still shows, its line says what was tried (*No active employee record
  matched this login by internal id …, e-mail …, name …*): make sure that
  person has an active employee record with the login's e-mail address (or
  the same name), then reload. The counts stay in the browser and are
  counted as pending (*Not saved (N lines)*) until then, and go up as soon as
  a save is accepted. The footer shows the raw id the page load saw
  (`v… · user -5`); the execution log records the outcome at Audit level
  (`invcount: signed-in user is employee -5`, `… login matched to employee
  N`, or `… request without a usable user id`).
- **"Not saved (N lines) — retrying: …"** in the header — the device could not
  save to NetSuite, and since 2026-09-14.3 the header names the actual reason
  (a session that timed out, a permission problem, the record type briefly
  unreachable) instead of just "retrying" with no detail. Counts stay on the
  device and go up once the problem clears; do not close the tab while it
  says so (the browser warns). A session-timeout usually shows a "Server
  returned a non-JSON response" message (NetSuite handed back a login page
  instead of JSON) — signing in again and reloading the tab fixes it.
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

# BSG Inventory Count (Suitelet)

A phone / tablet / desktop page for entering physical counts and posting them
as an **Inventory Adjustment** — the "Physical Inventory Worksheet" report,
minus the paper. Source: `suitescript/bsg_inventory_count_sl.js`.

## What it does

1. **Search** items by style #, name, description, UPC or vendor code — any
   words, in any order (`royale 5` finds "Royale NFHS V25 Soccer Ball - Size 5";
   `0125566912 white` narrows a style to its white children). Only active
   inventory items (and assemblies) show; matrix *parents* never do, because
   stock lives on the color/size children.
2. **Count** — key the counted quantity next to a hit and press **Add** (Enter
   jumps to the next hit, so a keyboard or barcode scanner flows down a shelf).
   Every line lands on the **Sheet** tab with the current on-hand at the chosen
   location, an editable count, `−`/`+` buttons and the resulting delta. The
   sheet is saved in the browser, per location, so a refresh or a dead battery
   does not lose the count.
3. **Submit** — the page posts the sheet to the Suitelet, which re-reads each
   item's on-hand *at that moment* and creates one Inventory Adjustment whose
   lines bring on-hand to the counted quantity (`Adjust Qty. By = counted −
   on hand`). Items whose count already matches are left off; if nothing
   differs, no adjustment is created at all. The result page links straight to
   the transaction. Sheets over 200 lines post as several adjustments, one after
   another.

The adjustment is created **as the logged-in user** — that is the audit trail
you want on a count — and the transaction memo records who counted and when
(editable on the sheet). Each line's memo reads `Counted 20 (on hand 25)`.

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
     counting. Their role needs **Inventory Adjustment (Create)** plus
     **Items**, **Locations** and **Accounts** (View).
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
| `SEARCH_PAGE_SIZE` | `50` | Hits per page (a **Load more** button pages on). |
| `MAX_LINES_PER_ADJUSTMENT` | `200` | Bigger sheets post as several adjustments. |
| `MEMO_PREFIX` | `Physical count` | Default memo when the counter leaves it blank: `Physical count 2026-09-11 - Andrew Murray`. |

**Locations.** With *Multi-Location Inventory* on (it is), the header has a
location picker: on-hand quantities are per location and every adjustment line
carries it. The picker is remembered per browser and defaults automatically
when the account has a single active location. Without the feature the picker
disappears and account-wide on-hand is used.

## How to run a count

1. Open the link on whatever device is handy; pick the **location** once.
2. Search, key a count, **Add**. Repeat. Counts of **0** are valid (they zero
   the item out). Switch to the **Sheet** tab any time to review or fix lines
   (`×` removes one, **Clear sheet** removes all — nothing is posted until
   Submit).
3. If a count spans hours, **Refresh on-hand** on the sheet re-reads the current
   quantities so the deltas stay honest (submit re-reads them again anyway).
4. **Submit count…** → confirm → the adjustment link appears. Any lines the
   server refused stay on the sheet with the reason. The adjustment posts to
   5005 INVENTORY ADJUSTMENT; there is nothing to pick.

Several people can count at once from their own devices; each submit is its
own adjustment. Do not have two people count the *same* item at the same time
— the second submit would set the count to its own number, not add to the first.

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
- **Search finds nothing for an item that exists** — it is inactive, a matrix
  parent (count its children), a non-inventory item, or not set up at the chosen
  location.
- **Non-JSON response** errors on the page mean NetSuite returned an HTML error
  page; the deployment's execution log has the stack.

## Updating the script

Edit `suitescript/bsg_inventory_count_sl.js`, then either re-upload it over
the same File Cabinet file (Edit → Select File) or push it with the
**NetSuite File Cabinet Push** workflow (`src_dir` = `suitescript`, `script_id`
= this Suitelet's script id). No redeploy is needed — the page never caches
itself.

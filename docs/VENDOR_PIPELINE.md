# The vendor pipeline — one vendor, start to finish

Andy's spec, 2026-07-31:

> First I want all new items to be created first — but first the script would
> need to add any colors for the new items that are not in NetSuite already.
> For the new items that are being created, all information must be filled in.
> Once all new items are added, inventory, purchase price, and any other data
> (on-sale, closeout, etc) would need to be updated before moving on to the
> next vendor.

## Why the old design was wrong

The previous cycle ran seven independent workflows on guessed clock slots,
each doing one narrow job — and **item creation was not in it at all**.
Creation existed only as manual-dispatch scripts, pilot-capped at 5 styles,
and deliberately created a bare skeleton on the theory that "the nightly
back-fill will fill it in tomorrow."

The SanMar create-preview (2026-07-24) shows what that cost:

| | |
| --- | --- |
| SanMar feed | 4,080 styles / 161,210 SKUs |
| Present in NetSuite | **585 styles** / 41,827 SKU combos |
| Missing under existing parents | 4,686 SKUs |
| Missing entirely (net-new styles) | **114,697 SKUs across 3,495 styles** |

So roughly **three quarters of SanMar's line had never been created**, and
every nightly job we tuned was polishing the ~26% that happened to exist.

## The pipeline

`scripts/vendor_pipeline.py --vendor <name>` runs four phases in dependency
order. Nothing moves to the next vendor until phase 4 finishes.

### 1. DISCOVER
Diff the vendor's feed against the live catalogue: which styles and SKUs are
missing, and which colour/size option values the new items will reference.
Read-only.

### 2. OPTIONS
Create the colour/size list values the new items need — **first**, because a
matrix item cannot reference an option value that doesn't exist yet.

The important rule here, kept from the existing ensure-values logic: a name
that differs from an existing value only by punctuation or spacing is a
**variant**, not a new colour. `Black/ Red` → `Black/Red`, `Ash Grey` →
`ASH GREY`. Variants are remapped to the existing value and never created —
creating them is what produced the Forest/Forrest duplicate mess. Of the 174
colours SanMar's new children reference, **1** was genuinely new and 89 were
variants.

### 3. CREATE
Matrix parents via the REST record API, then one child per SKU via the BSG
matrix RESTlet (which resolves the parent by name and the colour/size values
by name). Parent-before-child is guaranteed per style.

Items are born with the **same native pricing rules the update phase uses**,
so nothing is created wrong and corrected later:

* Base Price = the higher of MAP and MSRP (was: MSRP alone, which under-priced
  every MAP-only SKU)
* Purchase Price = the **case** price, falling back to the piece price (was:
  piece price, which runs ~$1 higher — the original too-high-cost bug)
* `weight` from the feed's piece weight

Sale-aware cost and the On Sale flag need the dip feed's sale windows, which
this phase doesn't load; phase 4 runs minutes later in the same pipeline and
applies them.

New items land **active in NetSuite but not web-visible** (`isOnline: False`,
Andy's choice) — nothing reaches the storefront unreviewed.
`item_web_display_fix.py` turns display on once an item has a real image.

### 4. UPDATE
The vendor's full field pass over **all** of its items, new and existing:
inventory (total + per-warehouse), purchase price, Base Price, weight,
on-sale, closeout, every `custitem_*` field, and the lifecycle heartbeat.

This is deliberately the same script that always ran. It is the single source
of truth for "every field", so creation stays thin instead of duplicating ~30
field mappings that would inevitably drift apart.

## Vendor order

    sanmar → momentec → ua → ss → dcos → champro-csv

Order no longer decides any **value**. Pricing ownership follows the item's
Preferred Vendor flag (`scripts/pricing_ownership.py`), not whoever wrote
last, so the sequence is purely about pacing the work and can be reordered
freely.

## Chaining, not clock slots

Only the first vendor is scheduled; each run dispatches the next vendor when
it finishes (`.github/workflows/nightly-pipeline.yml`). The old design guessed
clock gaps wide enough for the previous job's worst case, and GitHub's cron
routinely fired 1–3 h late, bunching jobs together — that is what produced the
429 storms on 2026-07-31. Chaining makes the order hold no matter how late the
scheduler starts.

A vendor whose phase fails does **not** abort the chain: every phase is
diff-aware and idempotent, so the remaining vendors still run and the next
night picks up whatever was missed. The run still exits non-zero and files a
failure issue.

## Ramp

`CREATE_MAX_STYLES` caps net-new styles created per vendor per night —
default **300** (Andy chose "ramped in over nights"). SanMar's full catalogue
lands in roughly two weeks; set it to `0` for no cap once the first batches
look right.

## Current coverage

| Vendor | discover | options | create | update |
| --- | --- | --- | --- | --- |
| sanmar | ✅ | ✅ | ✅ | ✅ |
| momentec | — | — | — | ✅ |
| ua | — | — | — | ✅ |
| ss | — | — | — | ✅ |
| dcos | — | (inline) | ✅ | ✅ |
| champro-csv | — | — | — | ✅ |

The gaps are real and tracked in `vendor_pipeline.PIPELINE_GAPS`:

* **momentec** — matches on matrix options only (no barcode), so creation
  needs a style→parent mapping built first.
* **ua** — DC OneSource parts carry no style grid; the parent structure per
  style needs deciding before children can be created.
* **ss** — 195k-SKU feed; needs the same preview/diff stage SanMar has before
  anything is created.

Run `python scripts/vendor_pipeline.py --list` to see this live.

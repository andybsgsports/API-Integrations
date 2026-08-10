# Nightly schedule — SUPERSEDED by the chained pipeline

**2026-08-10 (Andy)**: the seven clock-staggered schedules below stay
**retired** (their schedule triggers remain commented out; manual
`workflow_dispatch` still works). The nightly is now the single chained
pipeline — `nightly-pipeline.yml`, scheduled **04:00 UTC** on the default
branch — which runs one vendor at a time in order
(sanmar → momentec → ua → ss → dcos → champro-csv), each run dispatching
the next. See `docs/VENDOR_PIPELINE.md` for the full spec.

`item-lifecycle` is **not** in the chain: it stays manual-dispatch until
Andy approves its dry-run report (what gets inactivated is his call).

Everything below is kept as the historical record of the stagger contract
the chain replaced — and of *why* clock slots failed (late cron firings
bunched writers into the shared concurrency group and caused 429 storms).

## Why the times matter

All writers share the `netsuite-writes` concurrency group, and GitHub keeps
only **one pending run per group** — a newer pending run cancels the older
pending one. So two schedules that overlap don't queue politely; one of them
silently disappears. The contract: **each slot's gap to the next must exceed
the predecessor's worst observed runtime.**

| UTC | Workflow | Typical | Worst observed |
| --- | --- | --- | --- |
| 04:00 | ua-backfill | minutes | minutes |
| 04:20 | dcos-backfill (TCK / Cap America / Mizuno) | minutes | minutes |
| 04:40 | champro-csv-backfill | minutes | minutes |
| 05:00 | momentec-backfill | ~25 min | ~25 min |
| 06:00 | sanmar-field-update | <1 h steady-state | ~3 h full pass |
| 09:30 | ss-backfill | small steady-state | ~4 h full pass |
| 14:00 | item-lifecycle | minutes | minutes |

item-lifecycle runs **last** on purpose: it inactivates items whose
`custitem_feed_last_seen` heartbeat is stale past 7 days, so every vendor
writer above must have stamped first. Every heartbeat source
(sanmar/ss/momentec/ua/champro/tck/capamerica/mizuno) is covered by the six
vendor slots — dcos-backfill carries the last three.

Full passes only recur every ~3 days (the heartbeat refresh in
`feed_seen.STALE_AFTER_DAYS`); other nights are diff-only and small since the
checkbox-compare fix (PR #80) and the Preferred-Vendor pricing ownership rule
(`scripts/pricing_ownership.py`, 2026-07-31) — before that rule, SanMar and
S&S re-priced the same ~13.5k shared items against each other every night,
which was most of both jobs' write volume and 429 pressure.

See `docs/NIGHTLY_FLOW.md` for the full per-run detail (feeds, matching,
fields written, ownership).

If a run overruns its slot chronically, move the following slots later rather
than tightening `NETSUITE_WRITE_CONCURRENCY` blindly — and check the log for
sustained 429s first.

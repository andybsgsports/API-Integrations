# Nightly schedule — the stagger contract

**PAUSED 2026-07-31 (Andy)**: all seven schedule triggers are commented out in
the workflow files pending a redesign of the sync/pricing behaviour. The slots
below are the contract to restore when re-enabling. Manual `workflow_dispatch`
still works.

Re-enabled 2026-07-30 (previously paused while the syncs were broken). Scope
per Andy: **inventory and item status only** — the enrichment jobs
(description-update, atlas-image-backfill, parent-sync, sanmar-autocreate)
stay manual-dispatch.

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
checkbox-compare fix (PR #80).

If a run overruns its slot chronically, move the following slots later rather
than tightening `NETSUITE_WRITE_CONCURRENCY` blindly — and check the log for
sustained 429s first.

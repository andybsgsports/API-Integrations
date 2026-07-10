# Operational Runbook

## Daily / hourly jobs

| When (PT) | Command | Notes |
|-----------|---------|-------|
| 7:00 AM | `sanmar-sync all` | downloads files, then catalog → pricing → inventory |
| Hourly (business hrs) | `sanmar-sync sync-inventory` | availability from `sanmar_dip.txt` |
| Hourly (optional) | `sanmar-sync sync-live-pricing` | sale-aware base price |

`all` exits non-zero if any record failed — wire that to your alerting.

### Example cron

```cron
# Daily full sync at 7:00 AM Pacific
0 7 * * *  cd /opt/sanmar-netsuite && .venv/bin/sanmar-sync all >> /var/log/sanmar/daily.log 2>&1
# Hourly availability, 6 AM–8 PM Pacific
0 6-20 * * *  cd /opt/sanmar-netsuite && .venv/bin/sanmar-sync sync-inventory >> /var/log/sanmar/inv.log 2>&1
```

## Sandbox → production promotion

The integration is locked to sandbox by two gates (see README "Sandbox-first
safety"): `SYNC_DRY_RUN` and the production guardrail. Promote only after
sandbox sign-off:

1. **Sandbox**: `NETSUITE_ACCOUNT_ID` = a `_SB1` realm,
   `NETSUITE_ALLOW_PRODUCTION_WRITES=false`. All testing happens here.
2. **Production**: switch `NETSUITE_ACCOUNT_ID` to the live realm **and** set
   `NETSUITE_ALLOW_PRODUCTION_WRITES=true`. Missing either gate, the client
   refuses to connect — by design.

## First-time go-live checklist

1. `NETSUITE_SETUP.md` steps 1–9 complete in **sandbox**.
2. `.env` filled; `SYNC_DRY_RUN=true`; `NETSUITE_ACCOUNT_ID` = sandbox `_SB1`.
3. `sanmar-sync download` — confirm files land in `downloads/`.
4. `sanmar-sync sync-catalog` (dry run) — review the logged payloads.
5. `export-csv` → CSV import the matrix items into sandbox.
6. `SYNC_DRY_RUN=false SYNC_MAX_RECORDS=2 sanmar-sync sync-catalog` — verify 2
   items in **sandbox** (guardrail allows it because the realm is `_SB1`).
7. Remove the cap; run `all`. Validate prices, images, availability on a few SKUs.
8. Sign off, then promote per "Sandbox → production promotion" above.
9. Schedule the jobs.

## Resetting the delta cache

The SQLite cache at `STATE_DB_PATH` makes runs skip unchanged records. To force
a full re-push (e.g. after changing field mappings):

```bash
rm state/sync_state.db        # next run repushes everything
```

Safe to delete anytime — it only affects what gets *skipped*, never correctness.

## Common issues

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `User authenticating failed` from SanMar | wrong creds, or using sanmar.com login on SFTP | SFTP uses **customer number + FTP password**, not the web login |
| SFTP connect hangs / refused | wrong port or FTPS attempted | host `ftp.sanmar.com`, port **2200**, protocol **SFTP (SSH)** |
| `NetSuite 401 INVALID_LOGIN` | clock skew or bad token | sync server clock (OAuth1 timestamp); re-check consumer/token pairs + `NETSUITE_ACCOUNT_ID` realm |
| `No NetSuite item for SANMAR-… ; run catalog sync first` | inventory before catalog | run `sync-catalog` (or the CSV import) so the SKU exists |
| Matrix child rejected on REST create | building matrix structure via REST | create matrix items via the CSV Import Assistant; REST is for updates |
| Image upload 400 | wrong File Cabinet folder id / unsupported type | pass a valid `--folder` id; SanMar images are JPG |
| Everything skipped, nothing updates | warm delta cache | expected if SanMar didn't change; `rm` the cache to force |

## Planned maintenance

SanMar periodically takes integration platforms offline for maintenance (e.g.
the 05/14/2026 8–9 PM PT window). During an outage SFTP downloads will fail and
the retry/backoff will exhaust — the job exits non-zero and the next scheduled
run recovers automatically once SanMar is back. No manual action needed unless
failures persist across multiple windows.

## SanMar API change log

Tracked here so future maintainers know which announcements were evaluated
against this integration.

| Date | Ref# | Change | Impact on this integration |
|------|------|--------|----------------------------|
| 2026-06-05 | 457621 / 417453 | `getProductInfoByBrand` & `getProductInfoByCategory` become async-only; results delivered as `Type_BrandName_Date.csv` / `Type_CategoryName_Date.csv` on FTP. New `MAP_PRICE` column on `getProductBulkInfo`/`getProductDeltaInfo`/`getProductInfoByCategory`/`getProductInfoByBrand` CSVs; new `<mapPrice>` element on `getProductInfoByStyleColorSize` XML. | **None.** This integration uses the daily SFTP feed (`SanMar_SDL_N.csv`, `SanMar_EPDD.csv`, `sanmar_dip.txt`), not any of the impacted on-demand APIs. MAP is already parsed from `SanMar_SDL_N.csv` (`MAPPRICING` column) and mapped to `custitem_sanmar_map`. |

## Logs

Logs go to stderr with `LEVEL name :: message`. Set `LOG_LEVEL=DEBUG` to see the
exact NetSuite payloads (including in dry-run). Each sync ends with a one-line
summary: `catalog: processed=… created=… updated=… skipped=… failed=…`.

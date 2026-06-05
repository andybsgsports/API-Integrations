# SanMar → NetSuite Integration

Syncs the SanMar product catalog, images, pricing, and inventory availability
into NetSuite as **matrix inventory items**.

Built for Badger Sporting Goods (SanMar account **#298728**). Pulls SanMar's
daily/hourly data files over SFTP, transforms them, and upserts into NetSuite
via the SuiteTalk REST API — skipping anything unchanged since the last run.

> **Scope:** inbound product data only — catalog, images, pricing, inventory.
> Outbound purchase-order submission (`submitPO` / PromoStandards `SendPO`) is
> intentionally **out of scope** for this phase.

---

## What it does

| Sync | SanMar source | NetSuite target | Cadence |
|------|---------------|-----------------|---------|
| **Catalog** | `SanMar_SDL_N.csv` | Matrix parent (style) + child (color/size SKU) items | Daily |
| **Images** | image URLs in the catalog feed | Custom URL field (default) or File Cabinet upload | Daily |
| **Pricing** | `SanMar_SDL_N.csv` | Base / case / MSRP price levels + custom fields | Daily |
| **Live pricing** | `sanmar_dip.txt` (hourly) | Sale-aware base price | Hourly |
| **Inventory** | `sanmar_dip.txt` (hourly) | SanMar availability custom fields (not on-hand) | Hourly |

Why availability goes on **custom fields** rather than NetSuite's real on-hand
quantity: those warehouse counts are *SanMar's* stock, not yours. Writing them
to `quantityOnHand` would corrupt your books — that value is owned by inventory
transactions. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Quick start

```bash
# 1. Install (Python 3.10+)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
#   …fill in SanMar SFTP creds + NetSuite TBA tokens + field/account ids.

# 3. Dry-run against a sample (no NetSuite calls; SYNC_DRY_RUN defaults to true)
sanmar-sync sync-catalog --file tests/fixtures/sample_sdl_n.csv

# 4. Download today's real files from SanMar SFTP
sanmar-sync download

# 5. Generate the one-time matrix-item CSV for NetSuite's Import Assistant
sanmar-sync export-csv --file downloads/SanMar_SDL_N.csv --out data/matrix_items.csv

# 6. Go live (after the initial CSV load) — set SYNC_DRY_RUN=false in .env
sanmar-sync all
```

## CLI

```
sanmar-sync download [files...]      Download product files via SFTP
sanmar-sync list-remote [--dir]      List the SanMar SFTP folder
sanmar-sync sync-catalog   --file    Upsert styles/SKUs as matrix items
sanmar-sync sync-pricing   --file    Upsert regular pricing (catalog feed)
sanmar-sync sync-live-pricing --file Upsert sale-aware pricing (dip feed)
sanmar-sync sync-inventory --file    Upsert availability (dip feed)
sanmar-sync sync-images    --file --folder ID   Upload images to File Cabinet
sanmar-sync export-csv     --file --out         Generate matrix-item import CSV
sanmar-sync all                      Download + catalog + pricing + inventory
```

Omit `--file` and the tool downloads the right file from SFTP automatically.
Every write path honors `SYNC_DRY_RUN` (default **true**) — flip it to `false`
only once you've verified payloads.

---

## How it's built

```
SanMar SFTP ──► parsers ──► domain models ──► transforms ──► NetSuite REST
(SDL_N, EPDD,    (CSV /      (Style, Sku,     (item / price/  (SuiteTalk,
 dip.txt)         pipe)       Inventory)       availability)   TBA OAuth1)
                                   │
                              delta cache (SQLite) — skip unchanged records
```

```
src/sanmar_netsuite/
├── config.py            env-driven settings (SFTP, NetSuite, sync)
├── models.py            Style / Sku / Inventory / image dataclasses
├── cli.py               argparse entrypoint (sanmar-sync)
├── sanmar/
│   ├── constants.py     file names, warehouse map, dip column layout
│   ├── parsers.py       SDL_N / EPDD / dip readers
│   └── sftp_client.py   paramiko SFTP download (port 2200, SSH)
├── netsuite/
│   ├── client.py        SuiteTalk REST transport + TBA signing + SuiteQL
│   ├── repository.py    item upsert / lookup by external id
│   └── files.py         File Cabinet image upload
├── transform/
│   ├── catalog.py       style/SKU → item payloads
│   ├── pricing.py       price-level payloads (regular + sale-aware)
│   ├── inventory.py     availability custom-field payloads
│   └── csv_export.py    matrix-item CSV for the initial bulk load
├── state/cache.py       SQLite delta cache (hash + NetSuite id memo)
└── sync/                orchestration per entity
```

See [`docs/`](docs/) for architecture, NetSuite setup (custom fields, price
levels, integration record), and the operational runbook.

---

## Development

```bash
pytest          # unit + dry-run integration tests (no network needed)
ruff check src tests
mypy
```

Tests run entirely offline against fixtures in `tests/fixtures/`.

## Security notes

- Secrets live only in `.env` (git-ignored). Never commit real credentials.
- SanMar **SFTP** creds (customer number + FTP password) are distinct from the
  **sanmar.com** web-services login. Retrieve the SFTP password from the
  one-time Bitwarden Send link in onboarding and store it in your secret manager.
- Pin `SANMAR_SFTP_HOST_KEY` in production to prevent MITM on the SFTP session.
- NetSuite auth uses Token-Based Auth (OAuth 1.0a, HMAC-SHA256) — no passwords.

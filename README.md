# power_visual — CAISO bid-gap analysis + candidate plant map

Two connected workstreams (see [PLAN.md](PLAN.md) for the full design and verified
data-source research):

- **`pipeline/`** — Python (uv) batch pipeline: builds the plant database from public
  data, backfills CAISO DAM Public Bid Data (90-day delayed, pseudonymized),
  de-anonymizes a target plant's bids by fingerprinting against EPA CEMS hourly
  generation, and computes bid-vs-LMP gap statistics.
- **`map/`** — static MapLibre viewer (Vite, no API keys, OpenFreeMap tiles) of all
  CA CAISO plants with category coloring, filters, detail panel, and table view.
  Deploys as a static site (Vercel via git integration — do not deploy manually).

## Quick start

```bash
# plant database + map data (downloads sources into data/raw on first run)
uv run --project pipeline python pipeline/build_plants.py

# map dev server / build
cd map && npm install && npm run dev     # or: npm run build

# pipeline tests
cd pipeline && uv run pytest tests/ -q
```

## Pipeline scripts (run in this order)

| Script | Purpose |
|---|---|
| `oasis_client.py` | shared OASIS access layer: https, 5s pacing, 429 backoff, empty-report detection, report identity from gridstatus config |
| `resolve_resources.py` | plant → CAISO resource/node map with confidence tags (`manual`/`exact`/`prefix`) → `data/processed/resource_map.json` |
| `build_plants.py` | EIA-860/923 + OASIS ATL_RESOURCE + coastline → `map/public/plants.json` |
| `fetch_bids.py START END` | daily DAM Public Bid Data zips → reduced per-(seq,hour) parquet |
| `daily_pull.py [--max-days N]` | incremental pull: watermark + auto catch-up to `today − 90d`; runs daily via GitHub Actions (`.github/workflows/daily-pull.yml`) |
| `fetch_cems` (raw curl) | `data/raw/emissions-hourly-2025-ca.csv` from EPA CAMPD bulk files (no key) |
| `fingerprint.py --facility-id N` | rank masked bid seqs against a plant's CEMS run pattern |
| `fetch_lmp.py NODE START END` | DAM hourly LMP for the plant's pricing node |
| `bidgap_preview.py --seq N --lmp F` | hour classification + gap distribution + subsidy-recoverable MWh |

## Data notes (hard-won, verified)

- OASIS moved to `https://oasis.caiso.com` in April 2025 (http:// still 302-redirects,
  at ~2x latency). Rate limit is CAISO-stated in its 429 body: retry after 5 seconds.
  An HTTP 200 with only an XML stub in the zip means "no data" (e.g. embargoed bid
  days), not an error — `oasis_client` returns None for it.
- Public Bid Data (`GroupZip`, `PUB_DAM_GRP`/`PUB_RTM_GRP` v3) identifies resources
  only by persistent pseudonym `RESOURCEBID_SEQ` — never by resource ID. Fingerprinting
  is required; match confidence must be reported with any downstream result.
- CEMS `Date`/`Hour` are plant-local **standard** time year-round; CAISO uses prevailing
  local time — fingerprint hour matching allows ±1h.
- EIA-860 Generator file column "RTO/ISO LMP Node Designation" links plants to CAISO
  nodes; OASIS `ATL_RESOURCE` (public, no login) maps resource IDs ↔ pricing nodes.
- `data/raw/` is a cache (gitignored); every input is re-downloadable from public URLs.

# Plan — CAISO Bid-Gap Analysis + Candidate Plant Map

> **Status (2026-07-05):** M1 complete — `pipeline/build_plants.py` builds 838 CA CISO
> plants from EIA-860/923 (validated against Ormond/Alamitos/Huntington), and `map/` is
> the working MapLibre viewer (light/dark, filters, detail panel, table view; e2e
> screenshot-tested). Deployment deferred: user will supply a git repo, Vercel links to
> it. **M2 complete** — 92 days of DAM bids backfilled (0 failures), fingerprint method
> validated on Alamitos CTs (seqs 133660/158253, Jaccard ≈ 0.91), rarely-run units shown
> unidentifiable from one summer (see `data/results/M2_FINDINGS.md`), bid-gap preview
> produced (median missed-hour gap $9.12/MWh). Tests: `pipeline/tests/` (13 passing).

Two workstreams from the spec PDF: (A) a batch data pipeline that answers "what did the
target plant bid, what would it have needed to bid to clear, and how often/by how much did
it miss," and (B) a zero-cost, shareable map of candidate plants. They connect at one seam:
the pipeline emits per-plant summary JSON that the map displays.

**Everything below marked ✅ was verified by actually downloading the data (July 5, 2026).**

---

## Key findings from data verification

### 1. Public Bid Data endpoint — confirmed working ✅

- `http://oasis.caiso.com/oasisapi/GroupZip?groupid=PUB_DAM_GRP&startdatetime=YYYYMMDDT08:00-0000&version=3&resultformat=6`
- `PUB_DAM_GRP` (day-ahead) / `PUB_RTM_GRP` (real-time), one trade day per request, 90-day delay
- Also wrapped by `gridstatus` as `get_oasis_dataset("public_bids")`
- **Measured volumes:** DAM day = 432 KB zip → 12.4 MB CSV (~47k rows, ~1,600 resources);
  RTM day = 1.7 MB zip → 50.7 MB CSV. A year of both ≈ 800 MB compressed. Entirely
  manageable with Parquet/DuckDB on a laptop.
- Bid records include full bid curves (`SCH_BID_XAXISDATA` = MW, `SCH_BID_Y1AXISDATA` =
  $/MWh, `BIDPRICE` curve type) and **`SELFSCHEDMW`** — so self-schedule detection is
  directly in the data, as the spec requires.

### 2. ⚠️ The bid data is anonymized — this reshapes the pipeline

Resources in Public Bid Data are identified only by a masked `RESOURCEBID_SEQ`, **not** by
Resource ID. This is deliberate, long-standing policy: CAISO's board adopted FERC's
pseudonym approach (per the 1999 "Public Release of ISO Bid Data" memo and the FERC
PJM/NYISO orders) — bidder names are withheld, but **the pseudonym must stay constant over
time** so each bidder can be tracked.

Verified empirically ✅:
- Day-to-day: 1,274 of 1,282 generator seqs overlap (99.4%) between 2026-03-01 and 03-02
- Year-over-year: 1,085 of 1,265 overlap (86%) between 2025-03-01 and 2026-03-01
  (residual ≈ plausible fleet turnover)

**Consequence:** "what did the target plant bid" requires a **de-anonymization step** —
fingerprinting the target plant's masked seq. This is tractable precisely because the
pseudonyms are persistent:

- **Capacity fingerprint:** max bid MW across the year ≈ plant Pmax (known from EIA-860)
- **Operating-hours fingerprint:** EPA CEMS publishes hourly, unit-level gross load for
  every fossil unit, **with real names**, quarterly at a 2–3 month lag (compatible with the
  90-day bid lag). A masked seq must have bid/self-scheduled in exactly the hours the plant
  actually ran. Over a year, hourly on/off patterns are essentially unique.
- **Outage fingerprint:** days the plant appears in CAISO's per-resource outage report must
  be days its seq bid zero/absent.
- Score all ~1,300 generator seqs against the target's fingerprint; expect one dominant
  match with a quantifiable confidence. Report the confidence, and the runner-up gap,
  alongside all downstream results.

This is the one research-grade (not deterministic) step in the pipeline. For gas plants of
distinctive size that run occasionally (exactly our candidates), it should be
high-confidence; it must still be validated per plant.

### 3. Everything else needed is public, keyless, and verified

| Need | Source | Verified |
|---|---|---|
| Resource ID ↔ node mapping | OASIS `ATL_RESOURCE` report (no login). CAISO Resource IDs are readable plant codes (`ALAMIT_7_UNIT 3`, `ETIWND_2_UNIT1`, …), so plant → Resource ID → PNode is direct | ✅ downloaded |
| Clearing prices | OASIS `PRC_LMP` v12 (DAM hourly by node; also RTM variants); `gridstatus get_lmp` wraps it | ✅ downloaded 24h × 5 components |
| Plant attributes (lat/lon, capacity, prime mover, status, **cooling water source**) | EIA-860 2024 detailed files — `eia.gov/electricity/data/eia860/xls/eia8602024.zip` (22 MB, HTTP 200); EIA-860M monthly for recency | ✅ URL live |
| Hourly actual generation (for fingerprinting + capacity factors) | EPA CAMPD CEMS hourly bulk files, `api.epa.gov/easey` (free api.data.gov key; listing endpoint responds even with DEMO_KEY) | ✅ API responds |
| Annual/monthly net generation → capacity factor screen | EIA-923 | standard |
| Per-resource outages | CAISO "Curtailed and Non-Operational Generators" report; `gridstatus get_curtailed_non_operational_generator_report()` | wrapped |
| Distance to coast | computed from Natural Earth coastline shapefile | trivial |

Note: CAISO's "Master Control Area Generating Capability List" library page is now an empty
login-walled stub — not needed; `ATL_RESOURCE` + readable Resource IDs cover identity.

### 4. Candidate/target plant context (answers "which plants")

The spec's attributes (distance to coast, water source, dispatch flag) match the
**once-through-cooling coastal gas fleet**. Current public status: **Alamitos** (AES),
**Huntington Beach** (AES), and **Ormond Beach** (GenOn) had OTC compliance extended only
through **Dec 31, 2026**, operating under the Strategic Reliability Reserve; Scattergood
got a 5-year extension but is LADWP (non-CAISO — excluded from bid analysis, can still be
on the map).

Proposed data-driven screen instead of hand-picking: all CAISO-participating
(ATL_RESOURCE-matched) gas plants from EIA-860, ranked by EIA-923 capacity factor,
flagged for coastal/OTC status. That produces the candidate list with the exact map
attributes as a by-product.

**Caveat for interpretation:** Strategic Reserve units are dispatched under emergency
programs partly outside normal market clearing — for those plants, "didn't clear" hours
need the reserve-dispatch context noted, or the subsidy math overstates the gap.

### 5. DAM vs RTM (answers "which market first")

Measured: RTM is ~4× DAM by volume — both easily handled, so volume is not the
deciding factor. DAM first is still the right call *semantically*: unit commitment and
most energy volume clear in DAM, and an hourly DAM bid-vs-LMP comparison is the clean
version of "what would it have needed to bid to clear." RTM adds 5-minute complexity for
marginal insight on plants that rarely run. Build DAM end-to-end; add RTM later if the
subsidy model needs it.

---

## Pipeline design (workstream A)

```
pipeline/
  build_plants.py   # EIA-860 + EIA-923 + ATL_RESOURCE + coastline -> plant database
                    #   (this IS the "existing plant database" — rebuilt from public data)
  fetch.py          # backfill + incremental: public bids (DAM), LMP, CEMS, outage reports
  fingerprint.py    # target plant -> RESOURCEBID_SEQ candidate scoring + confidence report
  classify.py       # per-hour: self-scheduled / cleared / offline / near-miss / far-miss
  export.py         # rollups -> data/results/*.parquet + map/public/plants.json
```

Per-hour classification (given the matched seq):

1. **Self-scheduled** — `SELFSCHEDMW` > 0; not a subsidy target
2. **Cleared** — min bid price ≤ DAM LMP at the plant's node (cross-check: CEMS shows it ran)
3. **Offline** — in the outage report / no bid submitted
4. **Near-miss** — lowest bid segment − LMP ≤ threshold ($5/$10/$25 MWh, configurable)
5. **Far-miss** — gap above threshold

Outputs per plant: hourly table + rollups (% hours per class, gap distribution,
**MWh-recoverable-at-$X/MWh-subsidy curve** — the subsidy model's direct input), plus the
fingerprint confidence block.

**Comparison context** (spec: local, not statewide): comparison set = other seqs whose
matched/likely plants sit in the same local capacity area, first approximated by same
LAP/sub-region + fuel type via the node mapping. Note anonymity limits precision here —
aggregate local bid distributions are still computable without de-anonymizing every peer.

## Map viewer (workstream B) — unchanged, plant DB now sourced

- Vite + React + MapLibre GL, OpenFreeMap tiles — no API key anywhere in the map
- `plants.json` generated by `build_plants.py` (coordinates, capacity, prime mover,
  CAISO-participation flag from ATL_RESOURCE match, distance to coast, water source from
  EIA-860, capacity factor from EIA-923; bid-gap stats joined as workstream A lands)
- Static regeneration (resolves the PDF's live-vs-static open item: the underlying bid data
  is 90 days stale by definition; nothing to stream)
- Deploy: Vercel free tier, static. Caveat: the URL is public to anyone who has it;
  password protection is a paid Vercel feature.

## Milestones

1. **M1 — Plant database + map MVP:** EIA-860/923 + ATL_RESOURCE + coastline →
   `plants.json` → MapLibre app on Vercel. No bid data involved; shareable in one session.
2. **M2 — Fingerprint spike (the risk item):** pick one distinctive coastal plant
   (e.g., Ormond Beach), pull 3–6 months of DAM public bids + CEMS hourly, attempt the
   seq match, quantify confidence. This validates or kills the core premise early.
3. **M3 — Classification:** LMP joins, hour classification, threshold sweep, subsidy curves
   for matched plant(s); extend backfill to 12 months.
4. **M4 — Join to map:** categories + bid-gap stats on the map.
5. **Later:** RTM, refined local comparison sets, incremental monthly updates.

## Remaining open items

1. **Confirm the candidate-screen criteria** (gas + CAISO + coastal/OTC + low CF is my
   proposal — adjust thresholds?), and confirm Ormond Beach as the M2 spike plant.
2. Fingerprint confidence bar: what's acceptable before we trust a plant's bid history —
   propose requiring a clear winner (e.g., >95% of operating hours consistent, runner-up
   materially worse) plus a manual sanity check per plant.
3. The PDF's "existing plant database" — now assumed **rebuilt from EIA-860/923** (M1).
   If a curated internal file exists anyway, it can replace/augment `build_plants.py` input.

---

# OASIS API Client — Hardening Plan (from `caiso-oasis-api-connection-spec.pdf`)

> **Status (2026-07-06): M5–M7 implemented.**
> M5: `pipeline/oasis_client.py` is the single OASIS access layer (https, 5s pacing,
> 429 backoff, empty-report→None); `fetch_bids.py`/`fetch_lmp.py`/`build_plants.py`
> migrated; LMP chunking now 31d via gridstatus (report identity delegated to its
> maintained config). M6: `resolve_resources.py` → `data/processed/resource_map.json`
> (methods: 5 manual / 134 exact / 88 prefix; caught a real prefix-heuristic false
> positive — plant 62116 had matched Utah's HUNTERP_* EIM resources instead of
> HNTGBH_2_PL1X3). M7: `daily_pull.py` + `_watermark.json` + `.github/workflows/
> daily-pull.yml` (15:20 UTC daily, auto-commit); historical catch-up Oct 2025→Apr 2026
> run through the same code path. Tests: 22 passing. One deviation from the plan text
> below: the Actions workflow does **not** regenerate `plants.json` daily — its inputs
> (EIA annual files) change yearly, and the meta timestamp would create noise commits;
> regeneration stays manual until M4 wires bid-gap results into the map.

The spec doc mostly formalizes constraints the pipeline already stumbled into and handled
ad hoc (rate limiting, 90-day delay, per-report date caps). It also surfaces three real
gaps: no shared/hardened client (each script hand-rolls requests), identity resolution is
a same-run heuristic rather than a cached artifact, and there's no incremental daily-pull
design at all. Findings below are all freshly verified against live OASIS, not re-asserted
from the spec doc or from last session's notes.

## What's already compliant

- CSV via `resultformat=6` everywhere — matches "CSV is generally easier to work with."
- `fetch_bids.py` already does one-trading-day-per-request for Public Bid Data and paces
  requests (currently 5s).
- No credentials anywhere — matches "no login, no approval process" for public data.

## New findings (verified 2026-07-06)

1. **OASIS moved to `https://` in production, April 2025** — found in CAISO's own
   "Readiness Notes: Upcoming Technology Updates for OASIS" (Rev. 03/18/25). The note
   explicitly states *no core OASIS functionality changed* — only the URL protocol.
   Every pipeline script still calls `http://oasis.caiso.com/...`, which works today only
   because it 302-redirects to `https://`. Measured cost of that redirect: **0.71s vs
   0.38s** per request — a real tax across a multi-hundred-request bid backfill. Fix:
   switch the base URL to `https://` in `fetch_bids.py`, `fetch_lmp.py`, and the
   `ATL_RESOURCE`/`ATL_PNODE` pulls.
2. **The exact rate limit, from CAISO's own error response:** hammering the API returns
   HTTP 429 with body `CAISO Acceptable Use Policy Violation. Please retry your request
   after 5 seconds.` — not a guess, CAISO states the number. Current 5s spacing in
   `fetch_bids.py` is already correct; `fetch_lmp.py` has no explicit pacing between its
   monthly chunks beyond a flat `time.sleep(5)` and should get the same treatment
   uniformly (see client design below).
3. **"No data isn't always an error" — confirmed, and it's actually informative for us.**
   Querying inside the 90-day embargo returns HTTP 200 with a 645-byte XML stub (an empty
   `OASISReport`, no CSV file inside), not an error status. A naive `if status != 200`
   check would misclassify this. Any client needs to check for an actual data file in the
   zip, not just the HTTP status — and for Public Bid Data specifically, an empty result
   *is* the embargo boundary, which is a useful signal (see incremental pull, below).
4. **`gridstatus` does cover Public Bid Data — confirmed by actually running it**, not
   just reading its source (which is as far as last session's research went):
   `CAISO().get_oasis_dataset("public_bids", date="2025-07-01", params={"groupid":
   "PUB_DAM_GRP"})` returns the identical 27-column schema our hand-rolled parser expects
   (`RESOURCEBID_SEQ`, `SELFSCHEDMW`, `SCH_BID_XAXISDATA`, etc.), and it already
   implements: per-dataset date-range chunking (31 days default, 1 day for
   `public_bids` — matches the spec doc's stated caps exactly), retry-with-backoff, and
   429-aware sleep. It still calls `http://` internally, so it doesn't fully close finding
   #1 on its own.
5. **`ATL_RESOURCE` is the correct target for "Master File / resource listing."** CAISO's
   Master File is the authoritative internal database; `ATL_RESOURCE` is the OASIS report
   that exposes it (confirmed via CAISO's own Master File documentation). No separate
   "master file" report identifier is being missed.
6. Minor: `fetch_lmp.py` chunks LMP requests at 28 days out of caution. The verified cap
   is 31 days (both the spec doc and `gridstatus`'s default agree) — widening it cuts
   request count by roughly 10% on multi-month pulls. Low priority, easy fix.

## Gaps to close

1. **Centralize OASIS access into one client** (`pipeline/oasis_client.py`), replacing
   the duplicated request/retry/unzip logic in `fetch_bids.py`, `fetch_lmp.py`, and
   `build_plants.py`'s `ATL_RESOURCE` pull. Responsibilities: `https://` base URL; 5s
   inter-request pacing; retry-with-backoff on 429 (reuse `gridstatus`'s pattern —
   sleep, escalate, cap retries); detect an empty-report response (small XML, no embedded
   data file) and return "no data" distinctly from raising; per-report date-range
   chunking so callers pass any range and get correctly-sized sub-requests automatically.
   **Recommendation: build this as a thin wrapper around `gridstatus.CAISO()`** for LMP,
   resource listing (`ATL_RESOURCE`), and Public Bid Data — it already does most of the
   above and is now live-verified to return the right shape for bids. Keep our own
   reduction/classification logic (`reduce_day`, `classify_hour`) unchanged; only the
   transport layer changes. This directly addresses the spec's "report identifiers/
   versions aren't stable long-term" risk by delegating that tracking to an
   actively-maintained OSS project instead of hardcoding versions ourselves.
2. **Identity resolution as a cached, confidence-tagged artifact**, not a same-run
   heuristic. `build_plants.py` currently guesses `resource_ids` per plant via a prefix
   match against `ATL_RESOURCE` — approximate, and not the same lookup actually used to
   fetch Ormond's/Alamitos's LMP in M2 (those node IDs were found by hand-grepping the
   CSV). Plan: a dedicated `pipeline/resolve_resources.py` that joins EIA-860's "RTO/ISO
   LMP Node Designation" column against `ATL_RESOURCE`, tags each match
   `exact` / `prefix-heuristic` / `manual`, and writes
   `data/processed/resource_map.json` once (spec: "one-time, or infrequently-refreshed,
   not something to redo on every pull"). `build_plants.py` and the fingerprint/LMP
   scripts all read this file instead of resolving independently. Manual overrides (like
   the two Ormond node IDs and the Alamitos CC node) get recorded here explicitly rather
   than living only in shell history.
3. **Incremental daily pull**, entirely unbuilt today (M2 was a one-shot backfill).
   Design: `pipeline/daily_pull.py` + a watermark file
   `data/processed/_watermark.json` (`{"bids_dam": "2025-09-30", ...}`). Each run computes
   `today - 90 days` as the newly-eligible day and pulls every day from
   `watermark + 1` through it.
   **Recommendation on the spec's open question (catch-up vs. accept-gaps): catch up
   automatically, uncapped.** Public Bid Data is retained by CAISO indefinitely and a
   day's pull is idempotent and cheap (~14MB, one request), so there's no real cost to
   backfilling a missed stretch, and silently accepting gaps would quietly corrupt the
   "how often did it miss" statistic the whole analysis is for. Emit a warning (not a
   failure) if the catch-up exceeds some large N (e.g. 30 days), since that likely means
   the schedule itself broke rather than one missed run.
4. **Scheduling.** The repo now deploys the map to Vercel (static hosting — not a fit for
   a Python cron job). Recommend a **GitHub Actions scheduled workflow** (daily cron) that
   runs `daily_pull.py` and commits the new parquet + regenerated `plants.json` back to
   `main`, which also naturally triggers a Vercel redeploy. Tradeoffs to flag: needs a
   token with repo-write scope (a default `GITHUB_TOKEN` in Actions is sufficient for
   same-repo commits — no new secret to manage); committing daily data files will grow
   repo history over time (acceptable at ~14MB/day of reduced parquet; revisit if this
   becomes a problem, e.g. by moving processed data to a release asset instead of git).
5. **Observability.** `daily_pull.py` should log one line per day attempted with outcome
   (`ok` / `empty` / `error`) and row count, so a run of "empty" results reads as "not
   published yet" rather than silent failure — directly using finding #3 above.

## Sequencing (continues M1–M4 above)

- **M5 — Client consolidation:** build `oasis_client.py` on `gridstatus`, migrate
  `fetch_bids.py`/`fetch_lmp.py`/`build_plants.py` to use it, switch to `https://`, widen
  LMP chunking to 31 days. Pure refactor — `pipeline/tests/` (13 tests) should pass
  unchanged since output schemas don't change; add a test asserting the client rejects a
  request naively on an empty-report response versus raising.
- **M6 — Resource resolution artifact:** `resolve_resources.py` +
  `data/processed/resource_map.json`, with Ormond/Alamitos's already-known-good node IDs
  encoded as the first manual overrides. `build_plants.py` reads from it instead of
  guessing inline.
- **M7 — Incremental pull:** `daily_pull.py` + watermark file + GitHub Actions workflow.
  Depends on M5 (needs the hardened client) and M6 (needs resource resolution to know
  which nodes/resources to pull LMP for beyond the two already validated).

## Open items

1. Confirm GitHub Actions is an acceptable place to run scheduled jobs for this project
   (vs. e.g. a personal machine cron, which is simpler to start but doesn't run if a
   laptop is closed).
2. Decide the retention/growth strategy for daily-committed parquet once M7 has been
   running a while (git history growth vs. release-asset storage) — not urgent, revisit
   after a few weeks of real data volume.

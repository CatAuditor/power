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

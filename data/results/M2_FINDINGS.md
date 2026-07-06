# M2 findings — fingerprint spike + bid-gap preview (2026-07-05)

Window: 2025-07-01 → 2025-09-30 (92 days, DAM). Inputs: 871,245 reduced seq-hours of
public bids (1,407 masked generator seqs), EPA CEMS hourly for facilities 315/335/350,
DAM LMP at SP15 hub + plant nodes. Full ranked tables: `fingerprint_315_alamitos.txt`,
`fingerprint_350_ormond.txt`.

## 1. The de-anonymization method works — when the unit runs enough

**Alamitos CTs (facility 315, ~80 run-days each):** clear winners.

| unit | best seq | cap ratio | run-hour coverage | clear-day Jaccard | runner-up Jaccard |
|---|---|---|---|---|---|
| CT1 | `133660` | 0.97 | 1.000 | **0.906** | 0.886 |
| CT2 | `158253`* | 0.79 | 0.998 | 0.910 | ~0.902 field |

*CT2's nominal top row (`173802`) bids only 174 hours — an artifact; `158253` is the
credible match. CT1/CT2 winners are complementary, consistent with the two CTs of the
AES Alamitos Energy Center block. Confidence: moderate-high for the pair; a 12-month
window should widen the margins.

## 2. Rarely-running units cannot be pinned from one summer of DAM data

**Ormond Beach (facility 350): units ran 2 days / 1 day in 92.** Three always-bidding
~600–800 MW seqs (`114630`, `123314`, `166600`) have perfect run coverage but the
clearing-pattern signal cannot separate them at n=2 run-days (Jaccard ≈ 0.02 across the
field). Same for Alamitos steam units 4/5 (≤3 run-days). **Implication for the subsidy
thesis:** Strategic-Reserve-style units barely interact with the DAM — their "bid gap"
is not measurable from a short window, and their dispatch is partly out-of-market.
Remedies, in order: (a) extend the window back through 2022–2024 when these units ran
under RA contracts, (b) add RTM public bids, (c) treat the three-candidate set as an
ensemble (their bid curves may be similar enough to bound the gap).

## 3. Bid-gap preview (seq 133660 ≈ Alamitos CT1, DAM, node POD_ALAMIT_2_PL1X3-APND)

- 2,208 hours: **1,027 cleared** (bid in the money), **599 near-miss** (gap ≤ $10),
  541 far-miss, 41 no-bid; zero self-scheduled hours.
- Missed-hour gap distribution: median **$9.12/MWh**, p75 $17.10, max $38.74 — the
  misses are shallow, exactly the shape a small subsidy closes.
- Offered-MWh recoverable: **135 GWh at $5/MWh**, 201 GWh at $10, 340 GWh at $25.

Caveats: "cleared" is inferred from price vs LMP (awards aren't public); recoverable
MWh uses offered max MW (upper bound); marginal-unit feedback (subsidizing the bid
changes the clearing price) is out of scope until M3.

## Reproduce

```bash
uv run --project pipeline python pipeline/fetch_bids.py 2025-07-01 2025-09-30
uv run --project pipeline python pipeline/fetch_lmp.py "TH_SP15_GEN-APND" 2025-07-01 2025-09-30
uv run --project pipeline python pipeline/fingerprint.py --facility-id 315 --lmp data/processed/lmp/th_sp15_gen_apnd.parquet
uv run --project pipeline python pipeline/bidgap_preview.py --seq 133660 --lmp data/processed/lmp/pod_alamit_2_pl1x3_apnd.parquet
```

"""Fingerprint a target plant's masked RESOURCEBID_SEQ in CAISO public bid data.

Method (PLAN.md M2): public bids are pseudonymized but the pseudonym is persistent,
so match a plant's *observable* behavior to a seq:
  1. capacity  — seq's max offered MW over the window vs the unit's max CEMS gross load
  2. run cover — hours the unit actually ran (CEMS gross load > 0) must be hours the
                 seq was active in DAM (bid submitted or self-scheduled)
  3. run-day price behavior — on run days the seq should look dispatchable (self-sched
                 or lower min price), used as a soft tiebreaker

Timezone note: CEMS reports plant-local *standard* time year-round; CAISO bid
intervals are local prevailing time (PDT in summer). Hourly comparison therefore
allows +/-1h slack; day-level comparison is exact.

Discrimination note: coverage alone cannot separate candidates for units that ran
only a few days — every always-bidding seq trivially covers a small run set. The
--lmp option adds the discriminating signal: with a reference LMP series (SP15
hub), each seq gets a would-clear day pattern (days with any hour min_price <=
LMP, or self-scheduled) that is compared to the unit's actual run days (Jaccard),
plus the run-day price delta (did the seq bid cheaper on days the unit ran).

Usage: python fingerprint.py --facility-id 350 [--start 2025-07-01 --end 2025-09-30]
                             [--lmp data/processed/lmp/th_sp15_gen_apnd.parquet]
"""

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
BIDS = ROOT / "data" / "processed" / "bids_dam"
CEMS = ROOT / "data" / "raw" / "emissions-hourly-2025-ca.csv"
CEMS_CACHE = ROOT / "data" / "processed" / "cems_targets.parquet"


def load_cems_units(facility_id: int, start: str, end: str) -> pd.DataFrame:
    """Hourly gross load for each unit of the facility, filtered to window."""
    if CEMS_CACHE.exists():
        df = pd.read_parquet(CEMS_CACHE)
        df = df[df["Facility ID"] == facility_id]
    else:
        keep = ["Facility Name", "Facility ID", "Unit ID", "Date", "Hour", "Gross Load (MW)"]
        chunks = []
        for ch in pd.read_csv(CEMS, usecols=keep, chunksize=1_000_000,
                              dtype={"Date": str, "Unit ID": str, "Facility Name": str}, low_memory=False):
            ch = ch[ch["Facility ID"] == facility_id]
            if len(ch):
                chunks.append(ch)
        df = pd.concat(chunks, ignore_index=True)
    df = df[(df["Date"] >= start) & (df["Date"] <= end)]
    df = df.copy()
    df["gross_mw"] = pd.to_numeric(df["Gross Load (MW)"], errors="coerce").fillna(0.0)
    return df.rename(columns={"Unit ID": "unit", "Date": "date", "Hour": "hour"})


def load_bids(start: str, end: str) -> pd.DataFrame:
    files = sorted(BIDS.glob("*.parquet"))
    files = [f for f in files if start <= f.stem <= end]
    df = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
    df["ss_mw"] = pd.to_numeric(df["ss_mw"], errors="coerce").fillna(0.0)
    return df


def load_lmp_hourly(path: str) -> pd.DataFrame:
    lmp = pd.read_parquet(path)
    lmp = lmp.copy()
    lmp["hour"] = lmp["OPR_HR"].astype(int) - 1  # hour-ending 1..24 -> hour-start 0..23
    return lmp.rename(columns={"OPR_DT": "date"})[["date", "hour", "lmp"]]


def add_clearing_signal(out: pd.DataFrame, act: pd.DataFrame, lmp: pd.DataFrame,
                        run_days: set, all_days: set) -> pd.DataFrame:
    """Jaccard of would-clear days vs run days + run-day price behavior."""
    m = act.merge(lmp, on=["date", "hour"], how="left")
    m["would_clear"] = (m["min_price"] <= m["lmp"]) | (m["ss_mw"] > 0)
    jac, deltas = {}, {}
    for s, g in m.groupby("seq"):
        wc_days = set(g.loc[g["would_clear"], "date"])
        inter, union = len(wc_days & run_days), len(wc_days | run_days)
        jac[s] = inter / union if union else 0.0
        on = g[g["date"].isin(run_days)]["min_price"].median()
        off = g[~g["date"].isin(run_days)]["min_price"].median()
        deltas[s] = (on - off) if pd.notna(on) and pd.notna(off) else None
    out["clear_day_jaccard"] = out["seq"].map(jac).round(3)
    out["runday_price_delta"] = out["seq"].map(deltas).round(2)
    return out.sort_values(
        ["clear_day_jaccard", "run_hour_coverage"], ascending=False
    ).reset_index(drop=True)


def score_unit(unit_df: pd.DataFrame, bids: pd.DataFrame, label: str,
               lmp: pd.DataFrame | None = None) -> pd.DataFrame:
    run = unit_df[unit_df["gross_mw"] > 0]
    pmax = unit_df["gross_mw"].max()
    run_hours = set(zip(run["date"], run["hour"]))
    run_days = set(run["date"])
    n_days_total = bids["date"].nunique()
    print(f"\n=== {label}: pmax(CEMS)={pmax:.0f} MW, run hours={len(run_hours)}, run days={len(run_days)}/{n_days_total}")
    if not run_hours:
        print("unit never ran in window — fingerprint impossible for this unit")
        return pd.DataFrame()

    # hour set with +/-1h slack around bid activity
    active = bids[(bids["n_pts"] > 0) | (bids["ss_mw"] > 0)]
    seq_stats = active.groupby("seq").agg(max_mw=("max_mw", "max"), n_hours=("hour", "count"))

    # capacity prefilter: offered max within [0.55, 1.35] x CEMS pmax
    cands = seq_stats[(seq_stats["max_mw"] >= 0.55 * pmax) & (seq_stats["max_mw"] <= 1.35 * pmax)].index
    print(f"capacity band candidates: {len(cands)} of {len(seq_stats)} seqs")

    act = active[active["seq"].isin(cands)]
    act_hours = {s: set() for s in cands}
    for row in act.itertuples(index=False):
        h = int(row.hour)
        for hh in (h - 1, h, h + 1):
            act_hours[row.seq].add((row.date, hh))
    act_days = act.groupby("seq")["date"].agg(set)

    rows = []
    for s in cands:
        ah, ad = act_hours[s], act_days.get(s, set())
        cover_h = len(run_hours & ah) / len(run_hours)
        cover_d = len(run_days & ad) / len(run_days)
        ss_days = set(act[(act["seq"] == s) & (act["ss_mw"] > 0)]["date"])
        ss_run_days = len(ss_days & run_days)
        rows.append({
            "seq": s,
            "max_mw": seq_stats.loc[s, "max_mw"],
            "cap_ratio": seq_stats.loc[s, "max_mw"] / pmax,
            "active_hours": seq_stats.loc[s, "n_hours"],
            "run_hour_coverage": round(cover_h, 3),
            "run_day_coverage": round(cover_d, 3),
            "selfsched_days": ss_run_days,
        })
    out = pd.DataFrame(rows).sort_values(
        ["run_hour_coverage", "run_day_coverage"], ascending=False
    ).reset_index(drop=True)
    if lmp is not None and len(out):
        out = add_clearing_signal(out, act, lmp, run_days, set(bids["date"].unique()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--facility-id", type=int, required=True, help="EPA/EIA facility (ORIS) id")
    ap.add_argument("--start", default="2025-07-01")
    ap.add_argument("--end", default="2025-09-30")
    ap.add_argument("--lmp", default=None, help="reference LMP parquet (fetch_lmp.py) for clearing-pattern signal")
    args = ap.parse_args()
    lmp = load_lmp_hourly(args.lmp) if args.lmp else None

    cems = load_cems_units(args.facility_id, args.start, args.end)
    if cems.empty:
        raise SystemExit(f"no CEMS rows for facility {args.facility_id}")
    print("facility:", cems["Facility Name"].iloc[0], "| units:", sorted(cems["unit"].unique()))
    bids = load_bids(args.start, args.end)
    print(f"bids: {len(bids):,} seq-hours across {bids['date'].nunique()} days, {bids['seq'].nunique()} seqs")

    for unit, udf in cems.groupby("unit"):
        ranked = score_unit(udf, bids, f"unit {unit}", lmp=lmp)
        if len(ranked):
            print(ranked.head(8).to_string(index=False))


if __name__ == "__main__":
    main()

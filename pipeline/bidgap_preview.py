"""Bid-gap preview for one fingerprinted resource (PLAN.md M2/M3 bridge).

Joins the masked seq's reduced DAM bids to DAM LMP at the plant's node, hour by
hour, and classifies each hour:
  self-scheduled : SELFSCHEDMW > 0 (price-taker; not a subsidy target)
  cleared        : lowest bid price <= LMP (bid was in the money)
  near-miss      : 0 < (min bid - LMP) <= --near threshold
  far-miss       : gap above threshold
  no-bid         : seq absent that hour (offline / not offered)

"cleared" here is an approximation from prices (actual awards aren't in the
public file); CEMS run-hours give the ground-truth cross-check upstream.

Usage:
  python bidgap_preview.py --seq 123456 --lmp data/processed/lmp/<slug>.parquet \
      [--start 2025-07-01 --end 2025-09-30] [--near 10 --far 25]
Writes data/results/seq_<seq>_hourly.parquet and prints the summary.
"""

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
BIDS = ROOT / "data" / "processed" / "bids_dam"
RESULTS = ROOT / "data" / "results"


def classify_hour(n_pts, ss_mw, min_price, lmp, near: float) -> str:
    if pd.isna(n_pts):
        return "no-bid"
    if ss_mw > 0:
        return "self-scheduled"
    gap = min_price - lmp
    if gap <= 0:
        return "cleared"
    if gap <= near:
        return "near-miss"
    return "far-miss"


def load_bids_for_seq(seq: int, start: str, end: str) -> pd.DataFrame:
    files = [f for f in sorted(BIDS.glob("*.parquet")) if start <= f.stem <= end]
    if not files:
        raise SystemExit(f"no reduced bid files in {BIDS} for {start}..{end} — run fetch_bids.py first")
    parts = []
    for f in files:
        df = pd.read_parquet(f)
        parts.append(df[df["seq"] == seq])
    out = pd.concat(parts, ignore_index=True)
    if out.empty:
        raise SystemExit(f"seq {seq} has no bid rows in {start}..{end} — check the fingerprint output")
    out["ss_mw"] = pd.to_numeric(out["ss_mw"], errors="coerce").fillna(0.0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seq", type=int, required=True)
    ap.add_argument("--lmp", required=True, help="parquet from fetch_lmp.py")
    ap.add_argument("--start", default="2025-07-01")
    ap.add_argument("--end", default="2025-09-30")
    ap.add_argument("--near", type=float, default=10.0, help="near-miss gap $/MWh")
    ap.add_argument("--far", type=float, default=25.0, help="report far-miss bucket edge")
    args = ap.parse_args()

    bids = load_bids_for_seq(args.seq, args.start, args.end)
    lmp = pd.read_parquet(args.lmp)
    lmp = lmp[(lmp["OPR_DT"] >= args.start) & (lmp["OPR_DT"] <= args.end)].copy()
    # OPR_HR is hour-ending 1..24 local; bid 'hour' is hour-start 0..23 local
    lmp["hour"] = lmp["OPR_HR"].astype(int) - 1
    lmp = lmp.rename(columns={"OPR_DT": "date"})[["date", "hour", "lmp"]]

    df = lmp.merge(bids, on=["date", "hour"], how="left")
    df["class"] = df.apply(lambda r: classify_hour(r["n_pts"], r["ss_mw"], r["min_price"], r["lmp"], args.near), axis=1)
    df["gap"] = (df["min_price"] - df["lmp"]).where(df["class"].isin(["near-miss", "far-miss"]))

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"seq_{args.seq}_hourly.parquet"
    df.to_parquet(out, index=False)

    n = len(df)
    print(f"seq {args.seq}, {args.start}..{args.end}: {n} hours")
    print(df["class"].value_counts().to_string())
    missed = df[df["gap"].notna()]
    if len(missed):
        print("\ngap $/MWh distribution (missed hours):")
        print(missed["gap"].describe(percentiles=[0.25, 0.5, 0.75, 0.9]).round(2).to_string())
        print("\nMWh recoverable at subsidy X (offered MW in hours with 0 < gap <= X):")
        for x in sorted({5.0, 10.0, args.near, args.far, 50.0}):
            m = missed[missed["gap"] <= x]
            print(f"  X = ${x:>5.0f}/MWh: {m['max_mw'].sum():>10,.0f} MWh across {len(m):>4} hours")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()

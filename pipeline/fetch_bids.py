"""Backfill CAISO DAM Public Bid Data (90-day delayed) into reduced daily parquet.

Transport/pacing/empty-report handling live in oasis_client (PLAN.md M5); raw
zips are cached in data/raw/pub_bids_dam/. Reduced energy-bid summaries go to
data/processed/bids_dam/YYYY-MM-DD.parquet, one row per (resource seq, hour):
  seq, sc_seq, hour (local start), min_price, max_price, max_mw, ss_mw, n_pts

Usage: python fetch_bids.py 2025-07-01 2025-09-30
"""

import io
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from oasis_client import OasisClient

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "pub_bids_dam"
OUT = ROOT / "data" / "processed" / "bids_dam"


def reduce_day(zpath: Path, d: date) -> None:
    opath = OUT / f"{d}.parquet"
    if opath.exists():
        return
    with zipfile.ZipFile(zpath) as z:
        name = z.namelist()[0]
        df = pd.read_csv(io.BytesIO(z.read(name)), low_memory=False)
    df = df[(df["RESOURCE_TYPE"] == "GENERATOR") & (df["MARKETPRODUCTTYPE"] == "EN")]
    df = df.dropna(subset=["SCH_BID_TIMEINTERVALSTART"])
    hour = pd.to_datetime(df["SCH_BID_TIMEINTERVALSTART"]).dt.hour
    g = df.assign(hour=hour).groupby(["RESOURCEBID_SEQ", "hour"]).agg(
        sc_seq=("SCHEDULINGCOORDINATOR_SEQ", "first"),
        min_price=("SCH_BID_Y1AXISDATA", "min"),
        max_price=("SCH_BID_Y1AXISDATA", "max"),
        max_mw=("SCH_BID_XAXISDATA", "max"),
        ss_mw=("SELFSCHEDMW", "max"),
        n_pts=("SCH_BID_XAXISDATA", "count"),
    ).reset_index().rename(columns={"RESOURCEBID_SEQ": "seq"})
    g.insert(0, "date", str(d))
    g.to_parquet(opath, index=False)


def pull_day(client: OasisClient, d: date) -> str:
    """Fetch + reduce one trading day. Returns 'ok' | 'empty' | 'cached'."""
    if (OUT / f"{d}.parquet").exists():
        return "cached"
    zpath = client.download_public_bids_zip(d, RAW)
    if zpath is None:
        return "empty"
    reduce_day(zpath, d)
    return "ok"


def main(start: date, end: date):
    OUT.mkdir(parents=True, exist_ok=True)
    client = OasisClient()
    counts = {"ok": 0, "empty": 0, "cached": 0, "error": 0}
    d = start
    while d <= end:
        try:
            status = pull_day(client, d)
        except Exception as e:
            status = "error"
            print(f"{d}: error: {e}", flush=True)
        counts[status] += 1
        if status in ("empty", "error") or sum(counts.values()) % 10 == 0:
            print(f"{d}: {status}  (totals {counts})", flush=True)
        d += timedelta(days=1)
    print(f"done: {counts}", flush=True)


if __name__ == "__main__":
    main(date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2]))

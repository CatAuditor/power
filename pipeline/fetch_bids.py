"""Backfill CAISO DAM Public Bid Data (90-day delayed) into reduced daily parquet.

Raw zips cached in data/raw/pub_bids_dam/, reduced energy-bid summaries written to
data/processed/bids_dam/YYYY-MM-DD.parquet with one row per (resource seq, hour):
  seq, sc_seq, hour (local start), min_price, max_price, max_mw, ss_mw, n_pts

Usage: python fetch_bids.py 2025-07-01 2025-09-30
"""

import io
import sys
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "pub_bids_dam"
OUT = ROOT / "data" / "processed" / "bids_dam"
URL = ("http://oasis.caiso.com/oasisapi/GroupZip?groupid=PUB_DAM_GRP"
       "&startdatetime={d}T08:00-0000&version=3&resultformat=6")
SLEEP_S = 5.0  # OASIS returns 429 below ~5s spacing


def fetch_day(session: requests.Session, d: date) -> Path | None:
    zpath = RAW / f"{d}.zip"
    if zpath.exists() and zpath.stat().st_size > 10_000:
        return zpath
    for attempt in range(3):
        try:
            r = session.get(URL.format(d=d.strftime("%Y%m%d")), timeout=180)
            if r.status_code == 200 and len(r.content) > 10_000:
                zpath.write_bytes(r.content)
                time.sleep(SLEEP_S)
                return zpath
            print(f"{d}: HTTP {r.status_code}, {len(r.content)}B (attempt {attempt+1})", flush=True)
        except requests.RequestException as e:
            print(f"{d}: {e} (attempt {attempt+1})", flush=True)
        time.sleep(8 * (attempt + 1))
    return None


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


def main(start: date, end: date):
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    n_ok = n_fail = 0
    d = start
    while d <= end:
        zpath = fetch_day(session, d)
        if zpath:
            try:
                reduce_day(zpath, d)
                n_ok += 1
            except Exception as e:
                print(f"{d}: reduce failed: {e}", flush=True)
                n_fail += 1
        else:
            n_fail += 1
        if (n_ok + n_fail) % 10 == 0:
            print(f"progress: {n_ok} ok, {n_fail} failed, at {d}", flush=True)
        d += timedelta(days=1)
    print(f"done: {n_ok} ok, {n_fail} failed", flush=True)


if __name__ == "__main__":
    main(date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2]))

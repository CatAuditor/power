"""Fetch DAM hourly LMP for a node over a date range (monthly OASIS SingleZip chunks).

Usage: python fetch_lmp.py "POD_ORMOND_7_UNIT 1-APND" 2025-07-01 2025-09-30
Writes data/processed/lmp/<node-slug>.parquet
"""

import io
import re
import sys
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "processed" / "lmp"
# T07:00 UTC = midnight PDT: correct trade-day boundary for Apr-Oct windows.
# (Use T08:00 for PST-season windows; a mixed window clips one edge hour.)
URL = ("http://oasis.caiso.com/oasisapi/SingleZip?resultformat=6&queryname=PRC_LMP"
       "&version=12&market_run_id=DAM&node={node}"
       "&startdatetime={s}T07:00-0000&enddatetime={e}T07:00-0000")


def month_chunks(start: date, end: date):
    d = start
    while d <= end:
        nxt = min(d + timedelta(days=28), end)
        yield d, nxt + timedelta(days=1)  # end-exclusive
        d = nxt + timedelta(days=1)


def fetch(node: str, start: date, end: date) -> pd.DataFrame:
    frames = []
    for s, e in month_chunks(start, end):
        url = URL.format(node=quote(node), s=s.strftime("%Y%m%d"), e=e.strftime("%Y%m%d"))
        for attempt in range(4):
            r = requests.get(url, timeout=120)
            if r.status_code == 200 and r.content[:2] == b"PK":
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    name = z.namelist()[0]
                    if "INVALID" in name.upper():
                        raise SystemExit(f"OASIS rejected request: {z.read(name)[:300]}")
                    frames.append(pd.read_csv(io.BytesIO(z.read(name))))
                print(f"{s}..{e}: {len(frames[-1])} rows", flush=True)
                break
            print(f"{s}..{e}: HTTP {r.status_code}, retrying", flush=True)
            time.sleep(6 * (attempt + 1))
        time.sleep(5)
    df = pd.concat(frames, ignore_index=True)
    df = df[df["LMP_TYPE"] == "LMP"]
    df["ts"] = pd.to_datetime(df["INTERVALSTARTTIME_GMT"])
    df = df[["ts", "OPR_DT", "OPR_HR", "MW"]].rename(columns={"MW": "lmp"}).sort_values("ts")
    return df


if __name__ == "__main__":
    node, start, end = sys.argv[1], date.fromisoformat(sys.argv[2]), date.fromisoformat(sys.argv[3])
    OUT.mkdir(parents=True, exist_ok=True)
    df = fetch(node, start, end)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", node).strip("_").lower()
    p = OUT / f"{slug}.parquet"
    df.to_parquet(p, index=False)
    print(f"wrote {len(df)} rows -> {p}")

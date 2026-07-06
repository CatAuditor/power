"""Fetch DAM hourly LMP for a node over a date range.

Transport via oasis_client -> gridstatus (PRC_LMP v12, 31-day chunking,
pacing/retry). Output schema unchanged: ts, OPR_DT, OPR_HR, lmp.

Usage: python fetch_lmp.py "POD_ORMOND_7_UNIT 1-APND" 2025-07-01 2025-09-30
Writes data/processed/lmp/<node-slug>.parquet
"""

import re
import sys
from datetime import date
from pathlib import Path

from oasis_client import OasisClient

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "processed" / "lmp"


def node_slug(node: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", node).strip("_").lower()


if __name__ == "__main__":
    node, start, end = sys.argv[1], date.fromisoformat(sys.argv[2]), date.fromisoformat(sys.argv[3])
    OUT.mkdir(parents=True, exist_ok=True)
    df = OasisClient().get_lmp_dam(node, start, end)
    p = OUT / f"{node_slug(node)}.parquet"
    df.to_parquet(p, index=False)
    print(f"wrote {len(df)} rows -> {p}")

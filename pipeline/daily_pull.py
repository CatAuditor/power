"""Incremental daily pull of DAM Public Bid Data (PLAN.md M7).

Watermark semantics: data/processed/_watermark.json records the last trading
day successfully reduced. Each run pulls every day from watermark+1 through
today-90 (the newest possibly-published day given CAISO's fixed disclosure
rule). Missed runs therefore catch up automatically and idempotently — a
day's pull is one cached request, and silently accepting gaps would corrupt
the miss-rate statistics this dataset exists to support.

Stop conditions:
  empty : the day is still embargoed (HTTP 200, no data file) — normal; stop
          without advancing the watermark, next run retries.
  error : network/parse failure after retries — stop without advancing, exit
          nonzero so a scheduler flags the run.

Usage: python daily_pull.py [--max-days N]
"""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATERMARK = ROOT / "data" / "processed" / "_watermark.json"
BIDS_DIR = ROOT / "data" / "processed" / "bids_dam"
EMBARGO_DAYS = 90
CATCHUP_WARN_DAYS = 30


def load_watermark() -> date:
    if WATERMARK.exists():
        return date.fromisoformat(json.loads(WATERMARK.read_text())["bids_dam"])
    existing = sorted(BIDS_DIR.glob("*.parquet"))
    if not existing:
        raise SystemExit("no watermark and no existing bid parquet — run fetch_bids.py for an initial backfill first")
    return date.fromisoformat(existing[-1].stem)


def save_watermark(d: date) -> None:
    WATERMARK.parent.mkdir(parents=True, exist_ok=True)
    WATERMARK.write_text(json.dumps({"bids_dam": str(d)}) + "\n")


def run(watermark: date, target: date, pull_day, max_days: int | None = None):
    """Advance from watermark+1 toward target. Returns (new_watermark, results).

    pull_day(d) -> 'ok' | 'cached' | 'empty' (raises on error). results is a
    list of (date, status) for observability; one line per day attempted.
    """
    results = []
    span = (target - watermark).days
    if span > CATCHUP_WARN_DAYS:
        print(f"WARNING: catch-up span is {span} days (> {CATCHUP_WARN_DAYS}) — "
              f"schedule may have been broken", flush=True)
    d = watermark + timedelta(days=1)
    new_wm = watermark
    while d <= target:
        if max_days is not None and len(results) >= max_days:
            break
        try:
            status = pull_day(d)
        except Exception as e:
            results.append((d, f"error: {e}"))
            print(f"{d}: error: {e}", flush=True)
            return new_wm, results
        results.append((d, status))
        print(f"{d}: {status}", flush=True)
        if status == "empty":
            return new_wm, results
        new_wm = d
        d += timedelta(days=1)
    return new_wm, results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-days", type=int, default=None, help="limit days pulled this run (smoke tests)")
    args = ap.parse_args()

    from fetch_bids import pull_day as fetch_pull_day
    from oasis_client import OasisClient
    client = OasisClient()

    watermark = load_watermark()
    target = date.today() - timedelta(days=EMBARGO_DAYS)
    print(f"watermark={watermark} target={target}", flush=True)
    if watermark >= target:
        print("up to date — nothing eligible to pull", flush=True)
        return 0

    new_wm, results = run(watermark, target, lambda d: fetch_pull_day(client, d), max_days=args.max_days)
    if new_wm > watermark:
        save_watermark(new_wm)
    ok = sum(1 for _, s in results if s in ("ok", "cached"))
    errors = [r for r in results if str(r[1]).startswith("error")]
    print(f"done: {ok} day(s) ingested, watermark {watermark} -> {new_wm}", flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

"""Single hardened access layer for CAISO OASIS (PLAN.md M5).

Design decisions, all live-verified 2026-07-06:
  - Base URL is https:// (OASIS moved in April 2025; http:// now costs a 302
    redirect per request — 0.71s vs 0.38s measured).
  - Rate limit is CAISO-stated: 429 body says "retry your request after
    5 seconds". All requests are paced >= SLEEP_S apart; 429s back off and retry.
  - "No data" is HTTP 200 with a small XML stub (no CSV member in the zip) —
    e.g. any Public Bid Data day still inside the 90-day embargo. That is
    returned as None, distinct from an exception.
  - Report identifiers/versions for gridstatus-known datasets come from
    gridstatus's maintained OASIS_DATASET_CONFIG rather than being hardcoded
    here (the OASIS spec revises report names/versions across releases).
    High-volume Public Bid Data pulls use our own https transport so raw zips
    can be cached; low-volume reports (LMP) go through gridstatus's own
    fetcher, which still uses http:// internally — one redirect per 31-day
    chunk is accepted there. ATL_RESOURCE is not wrapped by gridstatus, so its
    identity (v1) lives here.
"""

import io
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

BASE = "https://oasis.caiso.com/oasisapi"
SLEEP_S = 5.0
PACIFIC = "US/Pacific"


def _pacific_utc(d: date) -> str:
    """Midnight Pacific on day d, formatted for OASIS (handles PST/PDT)."""
    ts = pd.Timestamp(d).tz_localize(PACIFIC).tz_convert("UTC")
    return ts.strftime("%Y%m%dT%H:%M-0000")


class OasisClient:
    def __init__(self, sleep_s: float = SLEEP_S):
        self.sleep_s = sleep_s
        self._session = requests.Session()
        self._last_request = 0.0

    # ---------- transport ----------

    def _paced_get(self, url: str, max_retries: int = 4) -> requests.Response:
        for attempt in range(max_retries):
            wait = self.sleep_s - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            r = self._session.get(url, timeout=180)
            self._last_request = time.monotonic()
            if r.status_code == 200:
                return r
            # 429 body: "CAISO Acceptable Use Policy Violation. Please retry
            # your request after 5 seconds."
            backoff = 6.0 * (attempt + 1)
            time.sleep(backoff)
        r.raise_for_status()
        return r  # unreachable when raise_for_status throws; keeps typing happy

    @staticmethod
    def extract_csv(content: bytes) -> pd.DataFrame | None:
        """CSV inside an OASIS zip, or None for an empty/error report.

        HTTP 200 with only an .xml member (empty OASISReport stub, or
        INVALID_REQUEST) means "no data for this query" — not an HTTP error.
        """
        try:
            z = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile:
            return None
        csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not csvs:
            return None
        return pd.read_csv(io.BytesIO(z.read(csvs[0])), low_memory=False)

    # ---------- reports ----------

    def download_public_bids_zip(self, d: date, cache_dir: Path,
                                 market: str = "DAM") -> Path | None:
        """One trading day of Public Bid Data; zip cached; None while embargoed."""
        from gridstatus.caiso.caiso_constants import OASIS_DATASET_CONFIG
        cfg = OASIS_DATASET_CONFIG["public_bids"]["query"]
        cache_dir.mkdir(parents=True, exist_ok=True)
        zpath = cache_dir / f"{d}.zip"
        if zpath.exists() and zpath.stat().st_size > 10_000:
            return zpath
        url = (f"{BASE}/GroupZip?groupid=PUB_{market}_GRP"
               f"&startdatetime={_pacific_utc(d)}"
               f"&version={cfg['version']}&resultformat={cfg['resultformat']}")
        r = self._paced_get(url)
        if self.extract_csv(r.content) is None:
            return None
        zpath.write_bytes(r.content)
        return zpath

    def get_lmp_dam(self, node: str, start: date, end: date) -> pd.DataFrame:
        """DAM hourly LMP for one node, [start, end] inclusive.

        Routed through gridstatus: report identity (PRC_LMP v12) and 31-day
        chunking are its responsibility. Output schema matches the pipeline's
        historical parquet: ts, OPR_DT, OPR_HR, lmp.
        """
        import gridstatus
        caiso = gridstatus.CAISO()
        df = caiso.get_oasis_dataset(
            "lmp_day_ahead_hourly",
            date=pd.Timestamp(start).tz_localize(PACIFIC),
            end=pd.Timestamp(end + timedelta(days=1)).tz_localize(PACIFIC),
            params={"node": node},
            raw_data=True,
            sleep=int(self.sleep_s),
        )
        df = df[df["LMP_TYPE"] == "LMP"].copy()
        df["ts"] = pd.to_datetime(df["INTERVALSTARTTIME_GMT"], utc=True)
        out = (df[["ts", "OPR_DT", "OPR_HR", "MW"]]
               .rename(columns={"MW": "lmp"})
               .drop_duplicates(subset=["OPR_DT", "OPR_HR"])
               .sort_values("ts")
               .reset_index(drop=True))
        out["OPR_DT"] = out["OPR_DT"].astype(str)
        return out

    def get_resource_listing(self, cache_path: Path | None = None) -> pd.DataFrame:
        """ATL_RESOURCE: resource ID <-> pricing node, effective-dated.

        Not wrapped by gridstatus; report identity (v1) verified live 2026-07.
        """
        if cache_path and cache_path.exists():
            return pd.read_csv(cache_path)
        today = date.today()
        url = (f"{BASE}/SingleZip?resultformat=6&queryname=ATL_RESOURCE&version=1"
               f"&startdatetime={_pacific_utc(today - timedelta(days=1))}"
               f"&enddatetime={_pacific_utc(today)}")
        r = self._paced_get(url)
        df = self.extract_csv(r.content)
        if df is None:
            raise RuntimeError("ATL_RESOURCE returned no data — query scope or report identity may need updating")
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(cache_path, index=False)
        return df

"""Tests for oasis_client empty-report handling and daily_pull watermark logic."""

import io
import zipfile
from datetime import date

import pandas as pd

import daily_pull
from oasis_client import OasisClient, _pacific_utc


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in members.items():
            z.writestr(name, content)
    return buf.getvalue()


# ---------- oasis_client ----------

def test_extract_csv_returns_dataframe():
    content = _zip_bytes({"20250701_PUB_BID_DAM_v3.csv": b"A,B\n1,2\n3,4\n"})
    df = OasisClient.extract_csv(content)
    assert isinstance(df, pd.DataFrame) and len(df) == 2


def test_extract_csv_empty_report_is_none_not_error():
    # HTTP 200 with only an XML stub = embargoed/no-data (verified live 2026-07-06)
    stub = b'<?xml version="1.0"?><m:OASISReport xmlns:m="x"><m:MessagePayload/></m:OASISReport>'
    assert OasisClient.extract_csv(_zip_bytes({"report.xml": stub})) is None


def test_extract_csv_non_zip_is_none():
    assert OasisClient.extract_csv(b"<html>CAISO Acceptable Use Policy Violation</html>") is None


def test_pacific_utc_handles_dst():
    assert _pacific_utc(date(2025, 7, 1)) == "20250701T07:00-0000"   # PDT
    assert _pacific_utc(date(2025, 1, 15)) == "20250115T08:00-0000"  # PST


# ---------- daily_pull ----------

def test_run_advances_watermark_and_stops_on_empty():
    statuses = {date(2025, 10, 1): "ok", date(2025, 10, 2): "ok", date(2025, 10, 3): "empty"}
    wm, results = daily_pull.run(date(2025, 9, 30), date(2025, 10, 5), lambda d: statuses[d])
    assert wm == date(2025, 10, 2)
    assert [s for _, s in results] == ["ok", "ok", "empty"]


def test_run_stops_on_error_without_advancing_past_it():
    def pull(d):
        if d == date(2025, 10, 2):
            raise RuntimeError("boom")
        return "ok"
    wm, results = daily_pull.run(date(2025, 9, 30), date(2025, 10, 5), pull)
    assert wm == date(2025, 10, 1)
    assert results[-1][1].startswith("error")


def test_run_catchup_warning(capsys):
    wm, _ = daily_pull.run(date(2025, 1, 1), date(2025, 3, 1), lambda d: "ok", max_days=1)
    assert "WARNING: catch-up span" in capsys.readouterr().out
    assert wm == date(2025, 1, 2)


def test_run_up_to_date_noop():
    wm, results = daily_pull.run(date(2025, 10, 5), date(2025, 10, 5), lambda d: "ok")
    assert wm == date(2025, 10, 5) and results == []


def test_run_cached_counts_as_progress():
    wm, results = daily_pull.run(date(2025, 9, 30), date(2025, 10, 1), lambda d: "cached")
    assert wm == date(2025, 10, 1)


def test_paced_get_retries_transient_network_errors():
    import requests as rq
    client = OasisClient(sleep_s=0)

    class FakeResp:
        status_code = 200
        content = b"x"

    calls = {"n": 0}

    class FakeSession:
        def get(self, url, timeout):
            calls["n"] += 1
            if calls["n"] < 3:
                raise rq.ConnectionError("read timed out")
            return FakeResp()

    client._session = FakeSession()
    import time as _t
    orig = _t.sleep
    _t.sleep = lambda s: None  # no real backoff waits in tests
    try:
        r = client._paced_get("https://example.invalid/x")
    finally:
        _t.sleep = orig
    assert r.status_code == 200 and calls["n"] == 3


# ---------- s3_sync ----------

def test_s3_sync_disabled_is_noop(monkeypatch):
    import s3_sync
    monkeypatch.delenv("POWER_DATA_BUCKET", raising=False)
    assert s3_sync.enabled() is False
    # no bucket -> functions no-op without touching the network
    assert s3_sync.push() == 0
    assert s3_sync.pull() == 0
    assert s3_sync.pull_watermark() is False


def test_s3_sync_targets_cover_bulk_paths():
    import s3_sync
    names = {t[0] for t in s3_sync.SYNC_TARGETS}
    assert names == {"bids_dam", "lmp", "cems_targets.parquet", "_watermark.json"}
    # resource_map.json and plants.json must NOT be synced (they stay in the repo)
    assert "resource_map.json" not in names

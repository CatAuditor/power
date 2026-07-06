from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import build_plants
import fetch_bids
import fingerprint

ROOT = Path(__file__).resolve().parent.parent.parent


# ---------- build_plants ----------

def test_haversine_known_distance():
    # SF (37.77,-122.42) to LA (34.05,-118.24) ~ 559 km
    d = build_plants.haversine_km(37.7749, -122.4194, 34.0522, -118.2437)
    assert 545 < d < 575


def test_dist_to_coast_sanity():
    coast = build_plants.coastline_points()
    assert len(coast) > 500
    # Huntington Beach pier is on the coast (NE 10m geometry is generalized: ~±3 km)
    assert build_plants.dist_to_coast_km(33.655, -118.003, coast) < 5
    # Fresno is far inland
    assert build_plants.dist_to_coast_km(36.74, -119.78, coast) > 80


@pytest.mark.parametrize(
    "is_fossil,cf,expected",
    [
        (False, 0.5, "non-thermal"),
        (True, None, "thermal-unknown"),
        (True, 0.05, "low-cf-thermal"),
        (True, 0.2, "mid-cf-thermal"),
        (True, 0.6, "high-cf-thermal"),
    ],
)
def test_classify(is_fossil, cf, expected):
    row = {"is_fossil": is_fossil, "capacity_factor": cf}
    assert build_plants.classify(row) == expected


def test_plants_json_output_schema():
    import json
    p = ROOT / "map" / "public" / "plants.json"
    # Python's json accepts NaN/Infinity but browsers' JSON.parse does not —
    # a bare NaN in the file breaks the whole map (caught live 2026-07-06).
    data = json.loads(p.read_text(),
                      parse_constant=lambda c: pytest.fail(f"non-JSON constant in plants.json: {c}"))
    assert data["meta"]["count"] == len(data["plants"]) > 700
    required = {"id", "name", "lat", "lon", "mw", "cf", "coast_km", "water", "category"}
    sample = data["plants"][0]
    assert required <= set(sample)
    cats = {pl["category"] for pl in data["plants"]}
    assert cats <= {"non-thermal", "low-cf-thermal", "mid-cf-thermal", "high-cf-thermal", "thermal-unknown"}
    for pl in data["plants"]:
        assert 32 < pl["lat"] < 43 and -125 < pl["lon"] < -113
        assert pl["mw"] >= 5


# ---------- fetch_bids ----------

def _any_cached_zip():
    zips = sorted((ROOT / "data" / "raw" / "pub_bids_dam").glob("*.zip"))
    return zips[0] if zips else None


@pytest.mark.skipif(_any_cached_zip() is None, reason="no cached bid zip yet")
def test_reduce_day_real_zip(tmp_path, monkeypatch):
    zpath = _any_cached_zip()
    monkeypatch.setattr(fetch_bids, "OUT", tmp_path)
    d = date.fromisoformat(zpath.stem)
    fetch_bids.reduce_day(zpath, d)
    out = pd.read_parquet(tmp_path / f"{d}.parquet")
    assert {"date", "seq", "hour", "min_price", "max_mw", "ss_mw", "n_pts"} <= set(out.columns)
    assert len(out) > 5000
    assert out["hour"].between(0, 23).all()
    assert not out.duplicated(["seq", "hour"]).any()
    assert out["min_price"].le(out["max_price"]).all()
    # energy bids only, generators only -> plausible seq count
    assert 500 < out["seq"].nunique() < 3000


# ---------- fingerprint ----------

def _synth_bids():
    rows = []
    days = [f"2025-07-{d:02d}" for d in range(1, 11)]
    for dt in days:
        for h in range(24):
            # seq 111: 700 MW unit that bids every hour
            rows.append(dict(date=dt, hour=h, seq=111, sc_seq=1, min_price=40.0,
                             max_price=90.0, max_mw=700.0, ss_mw=0.0, n_pts=3))
            # seq 222: 705 MW unit bidding only mornings (poor run coverage)
            if h < 8:
                rows.append(dict(date=dt, hour=h, seq=222, sc_seq=2, min_price=35.0,
                                 max_price=80.0, max_mw=705.0, ss_mw=0.0, n_pts=2))
            # seq 333: 100 MW unit (fails capacity band)
            rows.append(dict(date=dt, hour=h, seq=333, sc_seq=3, min_price=20.0,
                             max_price=25.0, max_mw=100.0, ss_mw=0.0, n_pts=2))
    return pd.DataFrame(rows)


def _synth_unit(run_hours):
    rows = []
    for dt, hrs in run_hours.items():
        for h in range(24):
            rows.append(dict(unit="1", date=dt, hour=h,
                             gross_mw=690.0 if h in hrs else 0.0))
    return pd.DataFrame(rows)


def test_fingerprint_picks_planted_seq(capsys):
    bids = _synth_bids()
    # unit ran afternoons on 3 days -> only seq 111 covers those hours
    unit = _synth_unit({"2025-07-02": {14, 15, 16}, "2025-07-05": {15, 16}, "2025-07-09": {17}})
    ranked = fingerprint.score_unit(unit, bids, "synthetic")
    assert ranked.iloc[0]["seq"] == 111
    assert ranked.iloc[0]["run_hour_coverage"] == 1.0
    # capacity band excluded the 100 MW seq
    assert 333 not in set(ranked["seq"])
    # morning-only bidder scores worse
    r222 = ranked[ranked["seq"] == 222].iloc[0]
    assert r222["run_hour_coverage"] < 0.5


def test_fingerprint_never_ran_unit_returns_empty():
    bids = _synth_bids()
    unit = _synth_unit({"2025-07-02": set()})
    ranked = fingerprint.score_unit(unit, bids, "idle")
    assert ranked.empty


# ---------- bidgap_preview ----------

def test_classify_hour():
    import numpy as np
    from bidgap_preview import classify_hour
    assert classify_hour(np.nan, 0, np.nan, 50.0, 10) == "no-bid"
    assert classify_hour(3, 200.0, 40.0, 50.0, 10) == "self-scheduled"
    assert classify_hour(3, 0.0, 40.0, 50.0, 10) == "cleared"
    assert classify_hour(3, 0.0, 55.0, 50.0, 10) == "near-miss"
    assert classify_hour(3, 0.0, 90.0, 50.0, 10) == "far-miss"
    # boundary: gap exactly 0 counts as cleared; gap exactly `near` is near-miss
    assert classify_hour(3, 0.0, 50.0, 50.0, 10) == "cleared"
    assert classify_hour(3, 0.0, 60.0, 50.0, 10) == "near-miss"


def test_fingerprint_clearing_signal_discriminates():
    # Both 111 and 222 bid ~700 MW every hour -> identical coverage.
    rows = []
    days = [f"2025-07-{d:02d}" for d in range(1, 11)]
    run_days = {"2025-07-03", "2025-07-07"}
    for dt in days:
        for h in range(24):
            # planted seq: cheap (in the money) only on run days
            rows.append(dict(date=dt, hour=h, seq=111, sc_seq=1,
                             min_price=20.0 if dt in run_days else 80.0,
                             max_price=90.0, max_mw=700.0, ss_mw=0.0, n_pts=3))
            # decoy: always cheap -> would clear every day
            rows.append(dict(date=dt, hour=h, seq=222, sc_seq=2, min_price=20.0,
                             max_price=80.0, max_mw=705.0, ss_mw=0.0, n_pts=2))
    bids = pd.DataFrame(rows)
    unit = _synth_unit({d: ({12, 13} if d in run_days else set()) for d in days})
    lmp = pd.DataFrame([dict(date=dt, hour=h, lmp=50.0) for dt in days for h in range(24)])
    ranked = fingerprint.score_unit(unit, bids, "synthetic-lmp", lmp=lmp)
    assert ranked.iloc[0]["seq"] == 111
    assert ranked.iloc[0]["clear_day_jaccard"] == 1.0
    assert ranked[ranked["seq"] == 222].iloc[0]["clear_day_jaccard"] < 0.5
    # planted seq bid cheaper on run days
    assert ranked.iloc[0]["runday_price_delta"] < 0

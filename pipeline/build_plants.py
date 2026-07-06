"""Build the candidate plant database -> map/public/plants.json

Sources (all public, downloaded into data/raw/ — see PLAN.md):
  - EIA-860 2024: plant coordinates, water source, BA code; generator capacity,
    prime mover, status, CAISO LMP node designation
  - EIA-923 2024: annual net generation -> capacity factor
  - CAISO OASIS ATL_RESOURCE: resource ID / node list (CAISO participation cross-check)
  - Natural Earth 10m coastline: distance to coast

Scope: California plants in the CISO balancing authority, thermal fleet focus,
but all CA CISO plants >= 5 MW are emitted so the map can filter.
"""

import json
import math
import re
from pathlib import Path

import pandas as pd
import shapefile

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "map" / "public"

MIN_PLANT_MW = 5.0

# EIA prime mover codes -> readable label
PRIME_MOVER = {
    "CA": "Combined cycle (steam)", "CT": "Combined cycle (turbine)",
    "CS": "Combined cycle (single shaft)", "CC": "Combined cycle",
    "GT": "Gas turbine (peaker)", "IC": "Internal combustion",
    "ST": "Steam turbine", "HY": "Hydro", "PS": "Pumped storage",
    "PV": "Solar PV", "WT": "Wind", "BA": "Battery", "ES": "Energy storage",
    "FC": "Fuel cell", "GE": "Geothermal (steam)", "BT": "Geothermal (binary)",
    "CP": "Solar thermal", "OT": "Other",
}

FOSSIL_SOURCES = {"NG", "DFO", "RFO", "JF", "KER", "PC", "SUB", "BIT", "LIG", "WC", "OG", "LFG"}


def load_plants() -> pd.DataFrame:
    df = pd.read_excel(RAW / "eia860" / "2___Plant_Y2024.xlsx", skiprows=1)
    df = df[(df["State"] == "CA")]
    cols = {
        "Plant Code": "plant_id", "Plant Name": "name", "City": "city",
        "County": "county", "Latitude": "lat", "Longitude": "lon",
        "Balancing Authority Code": "ba", "Name of Water Source": "water_source",
        "Sector Name": "sector",
    }
    df = df[list(cols)].rename(columns=cols)
    df = df.dropna(subset=["plant_id"])
    df["plant_id"] = df["plant_id"].astype(int)
    return df


def load_generators() -> pd.DataFrame:
    df = pd.read_excel(RAW / "eia860" / "3_1_Generator_Y2024.xlsx", sheet_name="Operable", skiprows=1)
    cols = {
        "Plant Code": "plant_id", "Generator ID": "gen_id", "Technology": "technology",
        "Prime Mover": "prime_mover", "Nameplate Capacity (MW)": "mw",
        "Summer Capacity (MW)": "summer_mw", "Status": "status",
        "Energy Source 1": "energy_source", "Operating Year": "op_year",
        "Planned Retirement Year": "retire_year",
        "RTO/ISO LMP Node Designation": "lmp_node",
    }
    df = df[list(cols)].rename(columns=cols)
    df = df.dropna(subset=["plant_id"])
    df["plant_id"] = df["plant_id"].astype(int)
    df["mw"] = pd.to_numeric(df["mw"], errors="coerce").fillna(0.0)
    for c in ("op_year", "retire_year"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # OP = operating, SB = standby, OS = out of service (retain: still relevant candidates)
    df = df[df["status"].isin(["OP", "SB", "OS"])]
    return df


def load_capacity_factors() -> pd.DataFrame:
    df = pd.read_excel(
        RAW / "eia923" / "EIA923_Schedules_2_3_4_5_M_12_2024_Final.xlsx",
        sheet_name="Page 1 Generation and Fuel Data", skiprows=5,
    )
    df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]
    df = df[df["Plant State"] == "CA"]
    g = df.groupby(df["Plant Id"].astype(int))["Net Generation (Megawatthours)"].sum()
    return g.rename("netgen_mwh")


def load_atl_resources() -> pd.DataFrame:
    df = pd.read_csv(RAW / "atl_resource.csv")
    return df[df["RESOURCE_TYPE"] == "GEN"]["RESOURCE_ID"].str.strip().to_frame()


def coastline_points() -> list[tuple[float, float]]:
    """Natural Earth 10m coastline vertices near California.

    Generalized geometry: distance-to-coast is accurate to roughly ±3 km, fine
    for screening. Includes SF Bay/Delta shoreline and the Channel Islands.
    """
    sf = shapefile.Reader(str(RAW / "coastline" / "ne_10m_coastline"))
    pts = []
    for shp in sf.shapes():
        for lon, lat in shp.points:
            if 30.0 <= lat <= 44.0 and -128.0 <= lon <= -115.0:
                pts.append((lat, lon))
    return pts


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def dist_to_coast_km(lat, lon, coast, coarse_step=50):
    # coarse pass over sampled vertices, fine pass around the winner
    best_i, best_d = 0, 1e9
    for i in range(0, len(coast), coarse_step):
        d = haversine_km(lat, lon, coast[i][0], coast[i][1])
        if d < best_d:
            best_i, best_d = i, d
    lo, hi = max(0, best_i - coarse_step), min(len(coast), best_i + coarse_step)
    for i in range(lo, hi):
        d = haversine_km(lat, lon, coast[i][0], coast[i][1])
        best_d = min(best_d, d)
    return round(best_d, 1)


def classify(row) -> str:
    """Placeholder category until bid-gap results exist (PLAN.md M4)."""
    if not row["is_fossil"]:
        return "non-thermal"
    cf = row["capacity_factor"]
    if cf is None or pd.isna(cf):
        return "thermal-unknown"
    if cf < 0.10:
        return "low-cf-thermal"      # rarely runs: prime subsidy-analysis candidates
    if cf < 0.40:
        return "mid-cf-thermal"
    return "high-cf-thermal"


def main():
    plants = load_plants()
    gens = load_generators()
    cf = load_capacity_factors()
    atl = load_atl_resources()
    coast = coastline_points()
    print(f"CA plants: {len(plants)}, generators: {len(gens)}, coast pts: {len(coast)}")

    agg = gens.groupby("plant_id").apply(
        lambda g: pd.Series({
            "nameplate_mw": g["mw"].sum(),
            "n_units": len(g),
            "prime_movers": sorted(g["prime_mover"].dropna().unique().tolist()),
            "technologies": sorted(g["technology"].dropna().unique().tolist()),
            "energy_sources": sorted(g["energy_source"].dropna().unique().tolist()),
            "is_fossil": bool(set(g["energy_source"].dropna()) & FOSSIL_SOURCES),
            "lmp_nodes": sorted({str(n).strip() for n in g["lmp_node"].dropna() if str(n).strip() and str(n).strip().lower() != "nan"}),
            "earliest_op_year": int(g["op_year"].min()) if g["op_year"].notna().any() else None,
            "retire_year": int(pd.to_numeric(g["retire_year"], errors="coerce").min()) if pd.to_numeric(g["retire_year"], errors="coerce").notna().any() else None,
        }),
        include_groups=False,
    ).reset_index()

    df = plants.merge(agg, on="plant_id", how="inner")
    df = df[df["ba"] == "CISO"]
    df = df[df["nameplate_mw"] >= MIN_PLANT_MW]
    df = df.merge(cf, left_on="plant_id", right_index=True, how="left")
    df["capacity_factor"] = (df["netgen_mwh"] / (df["nameplate_mw"] * 8760.0)).clip(lower=0).round(3)

    df["coast_km"] = [dist_to_coast_km(la, lo, coast) for la, lo in zip(df["lat"], df["lon"])]

    # CAISO resource-ID prefix heuristic from the utility-reported LMP node
    atl_ids = set(atl["RESOURCE_ID"])
    def resource_ids(nodes):
        found = set()
        for n in nodes:
            base = re.split(r"[ ,;]", str(n))[0].upper()
            prefix = base.split("_")[0][:6]
            if len(prefix) >= 4:
                found |= {r for r in atl_ids if r.startswith(prefix)}
        return sorted(found)[:12]
    df["caiso_resource_ids"] = df["lmp_nodes"].apply(resource_ids)

    df["category"] = df.apply(classify, axis=1)
    df = df.dropna(subset=["lat", "lon"])

    records = []
    for _, r in df.iterrows():
        records.append({
            "id": int(r["plant_id"]),
            "name": r["name"],
            "city": None if pd.isna(r["city"]) else r["city"],
            "county": None if pd.isna(r["county"]) else r["county"],
            "lat": round(float(r["lat"]), 5),
            "lon": round(float(r["lon"]), 5),
            "mw": round(float(r["nameplate_mw"]), 1),
            "units": int(r["n_units"]),
            "prime_movers": [PRIME_MOVER.get(p, p) for p in r["prime_movers"]],
            "pm_codes": r["prime_movers"],
            "tech": r["technologies"],
            "fossil": bool(r["is_fossil"]),
            "cf": None if pd.isna(r["capacity_factor"]) else float(r["capacity_factor"]),
            "coast_km": float(r["coast_km"]),
            "water": None if pd.isna(r["water_source"]) else str(r["water_source"]),
            "op_year": None if r["earliest_op_year"] is None else int(r["earliest_op_year"]),
            "retire_year": None if r["retire_year"] is None or pd.isna(r["retire_year"]) else int(r["retire_year"]),
            "lmp_nodes": r["lmp_nodes"],
            "resource_ids": r["caiso_resource_ids"],
            "category": r["category"],
            "bid_gap": None,  # populated by M3/M4 export
        })

    OUT.mkdir(parents=True, exist_ok=True)
    meta = {
        "generated": pd.Timestamp.now(tz="America/Los_Angeles").isoformat(),
        "sources": "EIA-860 2024, EIA-923 2024, CAISO OASIS ATL_RESOURCE, Natural Earth 10m",
        "filter": f"CA plants, BA=CISO, >= {MIN_PLANT_MW} MW",
        "count": len(records),
    }
    (OUT / "plants.json").write_text(json.dumps({"meta": meta, "plants": records}))
    print(f"wrote {len(records)} plants -> {OUT / 'plants.json'}")
    print(df["category"].value_counts().to_string())


if __name__ == "__main__":
    main()

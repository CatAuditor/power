"""Plant -> CAISO resource/node resolution as a cached artifact (PLAN.md M6).

The OASIS connection spec calls identity resolution "a prerequisite, not a data
pull itself... a one-time (or infrequently-refreshed) lookup per plant." This
module produces that lookup once — data/processed/resource_map.json — and every
other script reads it instead of re-deriving matches inline.

Each plant's entry is tagged with how it was resolved, in order of confidence:
  manual  : hand-verified against real OASIS pulls (e.g. the M2 fingerprint work)
  exact   : EIA-860's "RTO/ISO LMP Node Designation" string equals an
            ATL_RESOURCE RESOURCE_ID or NODE_ID after normalization
  prefix  : >=4-char prefix of the EIA node string matches RESOURCE_ID starts
            (the old build_plants heuristic — kept, but now visibly low-trust)
  none    : no match found

Usage: python resolve_resources.py           # rebuild map from current inputs
Output: data/processed/resource_map.json
"""

import json
import re
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = ROOT / "data" / "processed" / "resource_map.json"
ATL_CACHE = ROOT / "data" / "raw" / "atl_resource.csv"

# Hand-verified against live OASIS data during M2 (see data/results/M2_FINDINGS.md).
# These are the plants whose LMP/bid analysis has actually been run.
MANUAL_OVERRIDES: dict[int, dict] = {
    350: {  # Ormond Beach (GenOn) — LMP pulled at these nodes in M2
        "resource_ids": ["ORMOND_7_UNIT 1", "ORMOND_7_UNIT 2"],
        "nodes": ["POD_ORMOND_7_UNIT 1-APND", "POD_ORMOND_7_UNIT 2-APND"],
    },
    315: {  # AES Alamitos steam units (OTC fleet)
        "resource_ids": ["ALAMIT_7_UNIT 3", "ALAMIT_7_UNIT 4", "ALAMIT_7_UNIT 5"],
        "nodes": ["ALAMT3G_7_B1", "ALAMT4G_7_B1", "POD_ALAMIT_7_UNIT 5-APND"],
    },
    62115: {  # AES Alamitos Energy Center (CC block; fingerprinted seqs 133660/158253)
        "resource_ids": ["ALAMIT_2_PL1X3"],
        "nodes": ["POD_ALAMIT_2_PL1X3-APND"],
    },
    335: {  # AES Huntington Beach OTC steam unit 2 (CEMS unit "2")
        "resource_ids": ["HNTGBH_7_UNIT 2"],
        "nodes": ["POD_HNTGBH_7_UNIT 2-APND"],
    },
    62116: {  # AES Huntington Beach Energy Project CC block. NOTE: the prefix
        # heuristic matched HUNTERP_* here — those are Utah Huntington (PacifiCorp
        # EIM) resources, a false positive this override corrects.
        "resource_ids": ["HNTGBH_2_PL1X3"],
        "nodes": ["POD_HNTGBH_2_PL1X3-APND"],
    },
}


def norm(s: str) -> str:
    """Normalize an identifier for comparison: case, whitespace, trailing junk."""
    return re.sub(r"\s+", " ", str(s)).strip().strip("_").upper()


def resolve(plant_nodes: dict[int, list[str]], atl: pd.DataFrame,
            plant_names: dict[int, str] | None = None) -> dict:
    """Build the full resolution map.

    plant_nodes: {eia_plant_id: [EIA-860 'RTO/ISO LMP Node Designation' strings]}
    atl: ATL_RESOURCE dataframe (RESOURCE_ID, NODE_ID columns used)
    """
    atl = atl[atl["RESOURCE_TYPE"] == "GEN"] if "RESOURCE_TYPE" in atl else atl
    by_resource = {norm(r): r.strip() for r in atl["RESOURCE_ID"].astype(str)}
    by_node = {}
    for rid, nid in zip(atl["RESOURCE_ID"].astype(str), atl["NODE_ID"].astype(str)):
        by_node.setdefault(norm(nid), []).append((rid.strip(), nid.strip()))
    resource_list = sorted(by_resource.values())

    plants = {}
    counts = {"manual": 0, "exact": 0, "prefix": 0, "none": 0}
    for pid, nodes in plant_nodes.items():
        entry = {"name": (plant_names or {}).get(pid), "matched_from": nodes}
        if pid in MANUAL_OVERRIDES:
            entry.update(MANUAL_OVERRIDES[pid], method="manual")
        else:
            exact_rids, exact_nodes = set(), set()
            for n in nodes:
                key = norm(n)
                if key in by_resource:
                    exact_rids.add(by_resource[key])
                for rid, nid in by_node.get(key, []):
                    exact_rids.add(rid)
                    exact_nodes.add(nid)
            if exact_rids:
                entry.update(method="exact",
                             resource_ids=sorted(exact_rids),
                             nodes=sorted(exact_nodes))
            else:
                pref_rids = set()
                for n in nodes:
                    prefix = norm(n).split(" ")[0].split("_")[0][:6]
                    if len(prefix) >= 4:
                        pref_rids |= {r for r in resource_list if r.startswith(prefix)}
                if pref_rids:
                    entry.update(method="prefix", resource_ids=sorted(pref_rids)[:12], nodes=[])
                else:
                    entry.update(method="none", resource_ids=[], nodes=[])
        counts[entry["method"]] += 1
        plants[str(pid)] = entry

    return {
        "generated": str(date.today()),
        "sources": "EIA-860 2024 'RTO/ISO LMP Node Designation' x OASIS ATL_RESOURCE",
        "method_counts": counts,
        "plants": plants,
    }


def load_or_build(plant_nodes: dict[int, list[str]], atl: pd.DataFrame,
                  plant_names: dict[int, str] | None = None) -> dict:
    """Read the cached map if present; build and cache it otherwise."""
    if MAP_PATH.exists():
        return json.loads(MAP_PATH.read_text())
    m = resolve(plant_nodes, atl, plant_names)
    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAP_PATH.write_text(json.dumps(m, indent=1))
    return m


if __name__ == "__main__":
    # Standalone rebuild: pull inputs the same way build_plants does.
    import build_plants
    from oasis_client import OasisClient

    gens = build_plants.load_generators()
    nodes_by_plant = {}
    names = {}
    for pid, g in gens.groupby("plant_id"):
        vals = sorted({str(n).strip() for n in g["lmp_node"].dropna()
                       if str(n).strip() and str(n).strip().lower() != "nan"})
        if vals:
            nodes_by_plant[int(pid)] = vals
    plants = build_plants.load_plants()
    names = dict(zip(plants["plant_id"].astype(int), plants["name"]))

    atl = OasisClient().get_resource_listing(cache_path=ATL_CACHE)
    m = resolve(nodes_by_plant, atl, names)
    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAP_PATH.write_text(json.dumps(m, indent=1))
    print(f"wrote {MAP_PATH}")
    print("method counts:", m["method_counts"])

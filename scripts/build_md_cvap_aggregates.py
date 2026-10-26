"""Aggregate the 2020-block-disaggregated 2020-2024 ACS CVAP file."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path

from build_md_bef_crosswalks import DATA, ROOT, read_bef
from build_md_precinct_assets import FIPS_COUNTY
from build_md_vtd20_crosswalks import assign_blocks, load_vtd_polygons


SOURCE = DATA / "md_cvap_2024_2020_b_csv.zip"
OUT = DATA / "cvap_aggregates"
FIELDS = [
    "C_TOT24", "C_HSP24", "C_NHS24", "C_WHT24", "C_BLA24", "C_AMI24",
    "C_ASI24", "C_NHP24", "C_2OM24", "C_BLW24", "C_AIW24", "C_ASW24",
    "C_AIB24", "C_AIA24", "C_ASN24", "C_BLK24", "CVAP_TOT24", "CVAP_HSP24",
    "CVAP_NHS24", "CVAP_WHT24", "CVAP_BLA24", "CVAP_AMI24", "CVAP_ASI24",
    "CVAP_NHP24", "CVAP_2OM24", "CVAP_BLW24", "CVAP_AIW24", "CVAP_ASW24",
    "CVAP_AIB24", "CVAP_AIA24", "CVAP_ASN24", "CVAP_BLK24",
]


def pct(value: int, total: int) -> float:
    return round((value / total * 100.0), 6) if total > 0 else 0.0


def derived(values):
    total = int(values.get("CVAP_TOT24", 0))
    return {
        "total_population": int(values.get("C_TOT24", 0)),
        "vap_18plus": total,
        "cvap_total": total,
        "white_vap_pct": pct(int(values.get("CVAP_WHT24", 0)), total),
        "black_vap_pct": pct(int(values.get("CVAP_BLA24", 0)), total),
        "hispanic_vap_pct": pct(int(values.get("CVAP_HSP24", 0)), total),
        "native_vap_pct": pct(int(values.get("CVAP_AMI24", 0)), total),
        "asian_vap_pct": pct(int(values.get("CVAP_ASI24", 0)), total),
        "pacific_vap_pct": pct(int(values.get("CVAP_NHP24", 0)), total),
        "multiracial_vap_pct": pct(int(values.get("CVAP_2OM24", 0)), total),
        "white_cvap_pct": pct(int(values.get("CVAP_WHT24", 0)), total),
        "black_cvap_pct": pct(int(values.get("CVAP_BLA24", 0)), total),
        "hispanic_cvap_pct": pct(int(values.get("CVAP_HSP24", 0)), total),
        "native_cvap_pct": pct(int(values.get("CVAP_AMI24", 0)), total),
        "asian_cvap_pct": pct(int(values.get("CVAP_ASI24", 0)), total),
        "pacific_cvap_pct": pct(int(values.get("CVAP_NHP24", 0)), total),
        "multiracial_cvap_pct": pct(int(values.get("CVAP_2OM24", 0)), total),
    }


def aggregate(rows, assignment):
    totals = defaultdict(lambda: {field: 0 for field in FIELDS})
    unmatched = 0
    for row in rows:
        block = str(row.get("GEOID20") or "").strip()
        key = assignment.get(block)
        if not key:
            unmatched += 1
            continue
        node = totals[str(key)]
        for field in FIELDS:
            node[field] += int(float(row.get(field) or 0))
    return totals, unmatched


def write_csv(filename: str, id_field: str, totals, labels=None):
    path = OUT / filename
    derived_fields = list(derived({}).keys())
    label_fields = list(next(iter(labels.values())).keys()) if labels else []
    fieldnames = [id_field, *label_fields, *FIELDS, *derived_fields]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for key, values in sorted(totals.items()):
            writer.writerow({id_field: key, **(labels.get(key, {}) if labels else {}), **values, **derived(values)})
    print(f"wrote {path.relative_to(ROOT)} ({len(totals)} rows)")
    return path


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(SOURCE) as archive:
        member = next(name for name in archive.namelist() if name.lower().endswith(".csv"))
        with archive.open(member) as raw:
            rows = list(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")))

    block_ids = [str(row["GEOID20"]) for row in rows]
    county_assignment = {block: block[2:5] for block in block_ids}
    county_totals, county_missing = aggregate(rows, county_assignment)
    county_labels = {
        fips: {"county_name": FIPS_COUNTY.get(fips, fips), "county_norm": FIPS_COUNTY.get(fips, fips).upper()}
        for fips in county_totals
    }
    write_csv("county_2020__cvap24.csv", "county_fips", county_totals, county_labels)

    vtd20 = load_vtd_polygons(DATA / "tl_2020_24_vtd20.zip", "COUNTYFP20", "VTDST20")
    block_vtd, vtd_diagnostics = assign_blocks(
        vtd20, DATA / "tl_2020_24_tabblock20.zip", "GEOID20", "COUNTYFP20",
        "INTPTLON20", "INTPTLAT20", "ALAND20"
    )
    precinct_assignment = {block: target for block, (target, _area) in block_vtd.items()}
    precinct_totals, precinct_missing = aggregate(rows, precinct_assignment)
    write_csv("precinct_2020__cvap24.csv", "precinct_id", precinct_totals)

    plans = {
        "cd118_2022_lines__cvap24.csv": ("congressional", "SB1012-2022-Md-Congress-BEQ.xlsx"),
        "cd119_2024_lines__cvap24.csv": ("congressional", "SB1012-2022-Md-Congress-BEQ.xlsx"),
        "cd_mcrc_2021_lines__cvap24.csv": ("congressional", "MCRC-2021-Final-Congressional-BEF.xlsx"),
        "state_house_2022_lines__cvap24.csv": ("state_house", "MD-2022OFFICIALLYADOPTEDHOUSE.xlsx"),
        "state_house_2024_lines__cvap24.csv": ("state_house", "MD-2022OFFICIALLYADOPTEDHOUSE.xlsx"),
        "state_senate_2022_lines__cvap24.csv": ("state_senate", "MD-2022OFFICIALLYADOPTEDSENATE.xlsx"),
        "state_senate_2024_lines__cvap24.csv": ("state_senate", "MD-2022OFFICIALLYADOPTEDSENATE.xlsx"),
    }
    plan_diagnostics = {}
    for filename, (scope, bef_name) in plans.items():
        totals, unmatched = aggregate(rows, read_bef(DATA / bef_name, scope))
        write_csv(filename, "district", totals)
        plan_diagnostics[filename] = {"rows": len(totals), "unmatched_blocks": unmatched}

    county_demo = {"source": "2020-2024 ACS CVAP special tabulation disaggregated to 2020 blocks", "counties": {}}
    for fips, values in sorted(county_totals.items()):
        name = FIPS_COUNTY.get(fips, fips)
        d = derived(values)
        county_demo["counties"][name.upper()] = {
            "county": name, "county_fips": fips,
            "citizen_total": int(values["C_TOT24"]), "cvap_total": int(values["CVAP_TOT24"]),
            **{key: value for key, value in d.items() if key.endswith("_pct")},
            # The map's older demographic renderer reads *_pop_pct. Use the CVAP
            # composition until that renderer is fully renamed.
            "white_pop_pct": d["white_cvap_pct"], "black_pop_pct": d["black_cvap_pct"],
            "hispanic_pop_pct": d["hispanic_cvap_pct"], "native_pop_pct": d["native_cvap_pct"],
            "asian_pop_pct": d["asian_cvap_pct"], "pacific_pop_pct": d["pacific_cvap_pct"],
            "multiracial_pop_pct": d["multiracial_cvap_pct"],
        }
    (OUT / "county_2020__demographics_cvap24.json").write_text(json.dumps(county_demo, indent=2) + "\n", encoding="utf-8")

    diagnostics = {
        "source": str(SOURCE.relative_to(ROOT)), "input_blocks": len(rows),
        "county_unmatched_blocks": county_missing, "precinct_unmatched_blocks": precinct_missing,
        "vtd20_assignment": vtd_diagnostics, "plans": plan_diagnostics,
        "statewide_cvap_total": sum(int(row.get("CVAP_TOT24") or 0) for row in rows),
    }
    (OUT / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {(OUT / 'county_2020__demographics_cvap24.json').relative_to(ROOT)}")
    print(f"wrote {(OUT / 'diagnostics.json').relative_to(ROOT)}")


if __name__ == "__main__":
    main()

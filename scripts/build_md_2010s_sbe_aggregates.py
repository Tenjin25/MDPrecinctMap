"""Aggregate 2010-2014 Maryland SBE contests through the 2012 VTD chain."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone

from build_md_2000s_sbe_aggregates import aggregate, load_vote_rows
from build_md_2008_sbe_president_proxy import DATA, PLANS, norm_code


CROSSWALKS = DATA / "crosswalks_2012_vtd"
ELECTIONS = {
    2010: {
        "date": "20101102",
        "contests": {
            "governor": "Governor",
            "us_senate": "U.S. Senate",
            "comptroller": "Comptroller",
            "attorney_general": "Attorney General",
        },
    },
    2012: {
        "date": "20121106",
        "contests": {"president": "President", "us_senate": "U.S. Senate"},
    },
    2014: {
        "date": "20141104",
        "contests": {
            "governor": "Governor",
            "comptroller": "Comptroller",
            "attorney_general": "Attorney General",
        },
    },
}


def load_2012_crosswalk(plan: str):
    path = CROSSWALKS / f"vtd2012_to_{plan}.csv"
    by_vtd = defaultdict(list)
    county_fallback = defaultdict(lambda: defaultdict(float))
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            county = row["countyfp10"]
            code = norm_code(row["vtd_code"])
            district = row["district_num"]
            weight = float(row["allocation_weight"])
            by_vtd[(county, code)].append((district, weight))
            county_fallback[county][district] += weight
    for weights in county_fallback.values():
        total = sum(weights.values())
        if total:
            for district in list(weights):
                weights[district] /= total
    return by_vtd, county_fallback


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--mcrc-only", action="store_true")
    args = parser.parse_args()
    generated_at = datetime.now(timezone.utc).isoformat()
    promoted_entries = defaultdict(list)

    for year, election in ELECTIONS.items():
        output_dir = DATA / "district_contests_historical_proxy" / str(year)
        output_dir.mkdir(parents=True, exist_ok=True)
        local_manifest = []
        for contest_type, office in election["contests"].items():
            vote_rows, candidates = load_vote_rows(str(election["date"]), office)
            for plan, (scope, lines_year) in PLANS.items():
                if args.mcrc_only and plan != "congressional_2021_mcrc":
                    continue
                results, diagnostics, unmatched = aggregate(
                    vote_rows, candidates, plan, crosswalk_loader=load_2012_crosswalk
                )
                filename = f"{plan}_{contest_type}_{year}.json"
                payload = {
                    "meta": {
                        "status": "provisional_historical_proxy",
                        "scope": scope,
                        "contest_type": contest_type,
                        "year": year,
                        "district_lines_year": lines_year,
                        "vtd_vintage": 2012,
                        "generated_at_utc": generated_at,
                        "source": "Maryland SBE precinct returns via 2012 VTD and Census block chain",
                        "crosswalk": f"data/crosswalks_2012_vtd/vtd2012_to_{plan}.csv",
                        "county_constrained_aggregation": True,
                        "county_constrained_method": (
                            "Preserve each county/party total exactly; allocate only split-county "
                            "components with VTD/block weights; largest-remainder round within "
                            "each county/party; then sum county contributions into districts."
                        ),
                        "diagnostics": diagnostics,
                    },
                    "general": {"results": results},
                }
                (output_dir / filename).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                local_manifest.append({"plan": plan, "scope": scope, "contest_type": contest_type, "year": year, "file": filename, **diagnostics})

                if plan == "congressional_2022_enacted":
                    with (output_dir / f"unmatched_{contest_type}_{year}.csv").open("w", encoding="utf-8", newline="") as handle:
                        writer = csv.DictWriter(handle, fieldnames=["countyfp", "precinct", "votes"])
                        writer.writeheader()
                        writer.writerows(unmatched)

                if args.promote:
                    output_name = (
                        "district_contests_2021"
                        if plan == "congressional_2021_mcrc"
                        else "district_contests"
                    )
                    main_dir = DATA / output_name
                    main_filename = f"{scope}_{contest_type}_{year}.json"
                    (main_dir / main_filename).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                    promoted_entries[output_name].append(
                        {
                            "scope": scope,
                            "contest_type": contest_type,
                            "year": year,
                            "file": main_filename,
                            "rows": len(results),
                            "districts": len(results),
                            "total_votes": int(diagnostics["allocated_votes"]),
                            "dem_total": sum(int(row["dem_votes"]) for row in results.values()),
                            "rep_total": sum(int(row["rep_votes"]) for row in results.values()),
                            "other_total": sum(int(row["other_votes"]) for row in results.values()),
                            "major_party_contested": True,
                            "match_coverage_pct": diagnostics["direct_geographic_vote_coverage_pct"],
                            "provisional": True,
                            "vtd_vintage": 2012,
                            "allocation_method": "county_constrained_2012_vtd_block_chain_with_county_imputation",
                        }
                    )
                print(
                    f"{year} {contest_type} {plan}: "
                    f"{diagnostics['direct_geographic_vote_coverage_pct']:.2f}% direct geographic coverage"
                )
        local_manifest_path = output_dir / "manifest.json"
        if args.mcrc_only and local_manifest_path.exists():
            existing = json.loads(local_manifest_path.read_text(encoding="utf-8")).get("files", [])
            replacement_keys = {
                (row["plan"], row["contest_type"], int(row["year"])) for row in local_manifest
            }
            local_manifest = [
                row for row in existing
                if (row.get("plan"), row.get("contest_type"), int(row.get("year", 0)))
                not in replacement_keys
            ] + local_manifest
        local_manifest_path.write_text(json.dumps({"files": local_manifest}, indent=2) + "\n", encoding="utf-8")

    if args.promote:
        for output_name, entries in promoted_entries.items():
            manifest_path = DATA / output_name / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            keys = {(row["scope"], row["contest_type"], int(row["year"])) for row in entries}
            files = [
                row for row in manifest.get("files", [])
                if (row.get("scope"), row.get("contest_type"), int(row.get("year", 0))) not in keys
            ]
            files.extend(entries)
            files.sort(key=lambda row: (str(row["scope"]), str(row["contest_type"]), int(row["year"])))
            manifest["files"] = files
            manifest["source"] = (
                "Maryland SBE precinct/block election results + official Maryland BEF/VTD crosswalks"
            )
            manifest["generated_at_utc"] = generated_at
            manifest["includes_provisional_historical_slices"] = True
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            print(f"promoted {len(entries)} historical slices into data/{output_name}")


if __name__ == "__main__":
    main()

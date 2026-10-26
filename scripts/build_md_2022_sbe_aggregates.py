"""Aggregate 2022 Maryland SBE statewide contests onto modern district lines.

The 2022 SBE precinct codes are joined to the 2020-block precinct identifiers
used by the official-BEF crosswalks. County/party totals are conserved exactly;
only precincts split by a district boundary and non-geographic vote buckets are
allocated.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone

from build_md_2000s_sbe_aggregates import aggregate, load_vote_rows
from build_md_2008_sbe_president_proxy import COUNTY_FIPS, DATA, norm_code, norm_name


CROSSWALKS = DATA / "crosswalks_bef"
ELECTION = {
    "year": 2022,
    "date": "20221108",
    "contests": {
        "governor": "Governor",
        "us_senate": "U.S. Senate",
        "comptroller": "Comptroller",
        "attorney_general": "Attorney General",
    },
}
PLANS = {
    "congressional_2022_enacted": {
        "scope": "congressional",
        "lines_year": 2022,
        "crosswalk": "precinct_to_congressional_2022_enacted.csv",
        "output": "district_contests",
    },
    "congressional_2021_mcrc": {
        "scope": "congressional",
        "lines_year": 2021,
        "crosswalk": "precinct_to_congressional_2021_mcrc.csv",
        "output": "district_contests_2021",
    },
    "state_house_2022": {
        "scope": "state_house",
        "lines_year": 2022,
        "crosswalk": "precinct_to_state_house_2022.csv",
        "output": "district_contests",
    },
    "state_senate_2022": {
        "scope": "state_senate",
        "lines_year": 2022,
        "crosswalk": "precinct_to_state_senate_2022.csv",
        "output": "district_contests",
    },
}


def precinct_county_fips(precinct_key: str) -> str:
    normalized = norm_name(precinct_key)
    if normalized.startswith("STMARYS"):
        normalized = "SAINTMARYS" + normalized[len("STMARYS"):]
    for county_name, county_fips in sorted(COUNTY_FIPS.items(), key=lambda item: -len(item[0])):
        if normalized.startswith(county_name):
            return county_fips
    return ""


def load_bef_crosswalk(plan: str):
    path = CROSSWALKS / str(PLANS[plan]["crosswalk"])
    by_precinct = defaultdict(list)
    county_area = defaultdict(lambda: defaultdict(float))
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            precinct_key = str(row["precinct_key"])
            county = precinct_county_fips(precinct_key)
            # The RDH identifier contains both a short display code and the
            # zero-padded SBE code. The latter is the direct 2022 join key.
            codes = re.findall(r"\b\d{3}-\d{3}\b", precinct_key)
            code = norm_code(codes[0]) if codes else ""
            if not county or not code:
                continue
            district = str(row["district_num"])
            weight = float(row["area_weight"])
            by_precinct[(county, code)].append((district, weight))
            county_area[county][district] += float(row["land_area"] or 0)

    county_fallback = defaultdict(dict)
    for county, values in county_area.items():
        total = sum(values.values())
        if total:
            county_fallback[county] = {
                district: value / total for district, value in values.items()
            }
    return by_precinct, county_fallback


def manifest_entry(scope: str, contest_type: str, filename: str, results, diagnostics):
    return {
        "scope": scope,
        "contest_type": contest_type,
        "year": 2022,
        "file": filename,
        "rows": len(results),
        "districts": len(results),
        "total_votes": int(diagnostics["allocated_votes"]),
        "dem_total": sum(int(row["dem_votes"]) for row in results.values()),
        "rep_total": sum(int(row["rep_votes"]) for row in results.values()),
        "other_total": sum(int(row["other_votes"]) for row in results.values()),
        "major_party_contested": bool(
            sum(int(row["dem_votes"]) for row in results.values())
            and sum(int(row["rep_votes"]) for row in results.values())
        ),
        "match_coverage_pct": diagnostics["direct_geographic_vote_coverage_pct"],
        "precinct_crosswalk": True,
        "vtd_vintage": 2020,
        "allocation_method": "county_constrained_2022_precinct_to_2020_block_bef_crosswalk",
    }


def merge_manifest(output_name: str, entries: list[dict[str, object]]) -> None:
    output = DATA / output_name
    path = output / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    keys = {(row["scope"], row["contest_type"], int(row["year"])) for row in entries}
    files = [
        row for row in manifest.get("files", [])
        if (row.get("scope"), row.get("contest_type"), int(row.get("year", 0))) not in keys
    ]
    files.extend(entries)
    files.sort(key=lambda row: (str(row["scope"]), str(row["contest_type"]), int(row["year"])))
    manifest["files"] = files
    manifest["source"] = (
        "Maryland SBE precinct/block election results + official Maryland BEF crosswalks"
    )
    manifest["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["includes_2022_precinct_crosswalk_slices"] = True
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()
    generated_at = datetime.now(timezone.utc).isoformat()
    proxy_dir = DATA / "district_contests_historical_proxy" / "2022"
    proxy_dir.mkdir(parents=True, exist_ok=True)
    local_manifest = []
    promoted = defaultdict(list)

    for contest_type, office in ELECTION["contests"].items():
        vote_rows, candidates = load_vote_rows(str(ELECTION["date"]), office)
        for plan, plan_info in PLANS.items():
            results, diagnostics, unmatched = aggregate(
                vote_rows,
                candidates,
                plan,
                crosswalk_loader=load_bef_crosswalk,
            )
            proxy_filename = f"{plan}_{contest_type}_2022.json"
            payload = {
                "meta": {
                    "status": "modern_precinct_crosswalk",
                    "scope": plan_info["scope"],
                    "contest_type": contest_type,
                    "year": 2022,
                    "district_lines_year": plan_info["lines_year"],
                    "vtd_vintage": 2020,
                    "generated_at_utc": generated_at,
                    "source": "Maryland SBE 2022 precinct returns via 2020-block official-BEF crosswalk",
                    "input_file": "data/openelections/md_sbe/20221108__md__general__precinct.csv",
                    "crosswalk": f"data/crosswalks_bef/{plan_info['crosswalk']}",
                    "county_constrained_aggregation": True,
                    "county_constrained_method": (
                        "Preserve each county/party total exactly; allocate split precincts and "
                        "non-geographic buckets with county-local crosswalk evidence; largest-remainder "
                        "round within each county/party; then sum county contributions into districts."
                    ),
                    "diagnostics": diagnostics,
                },
                "general": {"results": results},
            }
            (proxy_dir / proxy_filename).write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
            local_manifest.append({
                "plan": plan,
                **manifest_entry(str(plan_info["scope"]), contest_type, proxy_filename, results, diagnostics),
            })

            if plan == "congressional_2022_enacted":
                unmatched_path = proxy_dir / f"unmatched_{contest_type}_2022.csv"
                with unmatched_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["countyfp", "precinct", "votes"])
                    writer.writeheader()
                    writer.writerows(unmatched)

            if args.promote:
                output_name = str(plan_info["output"])
                output_filename = f"{plan_info['scope']}_{contest_type}_2022.json"
                (DATA / output_name / output_filename).write_text(
                    json.dumps(payload, indent=2) + "\n", encoding="utf-8"
                )
                promoted[output_name].append(
                    manifest_entry(str(plan_info["scope"]), contest_type, output_filename, results, diagnostics)
                )

            print(
                f"2022 {contest_type} {plan}: "
                f"{diagnostics['direct_geographic_vote_coverage_pct']:.2f}% direct geographic coverage"
            )

    (proxy_dir / "manifest.json").write_text(
        json.dumps({"files": local_manifest}, indent=2) + "\n", encoding="utf-8"
    )
    if args.promote:
        for output_name, entries in promoted.items():
            merge_manifest(output_name, entries)
        print(f"promoted {sum(len(entries) for entries in promoted.values())} 2022 district slices")


if __name__ == "__main__":
    main()

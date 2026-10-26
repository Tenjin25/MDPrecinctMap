"""Aggregate 2000s Maryland SBE statewide contests through the 2008 VTD chain."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone

from build_md_2008_sbe_president_proxy import (
    COUNTY_FIPS,
    CROSSWALKS,
    DATA,
    PLANS,
    PSEUDO_PRECINCTS,
    load_crosswalk,
    norm_name,
    party_bucket,
    resolve_vtd,
)
from md_county_constrained import PARTIES, apportion_counties_to_districts


ELECTIONS = {
    2000: {
        "date": "20001107",
        "contests": {"president": "President", "us_senate": "U.S. Senate"},
    },
    2004: {
        "date": "20041102",
        "contests": {"president": "President", "us_senate": "U.S. Senate"},
    },
    2006: {
        "date": "20061107",
        "contests": {
            "governor": "Governor",
            "us_senate": "U.S. Senate",
            "comptroller": "Comptroller",
            "attorney_general": "Attorney General",
        },
    },
    2008: {"date": "20081104", "contests": {"president": "President"}},
}


def load_vote_rows(date: str, office: str) -> tuple[list[dict[str, object]], dict[str, str]]:
    source = DATA / "openelections" / "md_sbe" / f"{date}__md__general__precinct.csv"
    vote_rows: list[dict[str, object]] = []
    candidates: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["office"] != office:
                continue
            county = COUNTY_FIPS.get(norm_name(row["county"]))
            if not county:
                raise ValueError(f"Unknown county: {row['county']}")
            party = party_bucket(row["party"])
            votes = int(row["votes"] or 0)
            candidates[party][row["candidate"]] += votes
            vote_rows.append(
                {
                    "county": county,
                    "precinct": row["precinct"],
                    "party": party,
                    "votes": votes,
                    "pseudo": row["precinct"].strip().upper() in PSEUDO_PRECINCTS,
                }
            )
    candidate_names = {
        party: max(values.items(), key=lambda item: item[1])[0] if values else ""
        for party, values in candidates.items()
    }
    return vote_rows, candidate_names


def aggregate(
    vote_rows: list[dict[str, object]],
    candidate_names: dict[str, str],
    plan: str,
    crosswalk_loader=load_crosswalk,
) -> tuple[dict[str, dict[str, object]], dict[str, float], list[dict[str, object]]]:
    by_vtd, county_fallback = crosswalk_loader(plan)
    county_totals: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(float))
    )
    county_targets: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    direct_basis: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    pending: list[dict[str, object]] = []
    diagnostics: dict[str, float] = defaultdict(float)
    matched_keys: set[tuple[str, str]] = set()
    unmatched_keys: set[tuple[str, str]] = set()
    unmatched_votes: dict[tuple[str, str], int] = defaultdict(int)
    statewide_fallback: dict[str, float] = defaultdict(float)
    for district_weights in county_fallback.values():
        for district, weight in district_weights.items():
            statewide_fallback[district] += weight
    statewide_fallback_total = sum(statewide_fallback.values())
    if statewide_fallback_total:
        for district in list(statewide_fallback):
            statewide_fallback[district] /= statewide_fallback_total

    for row in vote_rows:
        county = str(row["county"])
        party = str(row["party"])
        votes = int(row["votes"])
        county_targets[county][party] += votes
        diagnostics["input_votes"] += votes
        if bool(row["pseudo"]):
            diagnostics["pseudo_votes"] += votes
            pending.append(row)
            continue
        allocation, method = resolve_vtd(county, str(row["precinct"]), by_vtd)
        key = (county, str(row["precinct"]))
        if not allocation:
            diagnostics["unmatched_geographic_votes"] += votes
            unmatched_keys.add(key)
            unmatched_votes[key] += votes
            pending.append(row)
            continue
        matched_keys.add(key)
        diagnostics["direct_geographic_votes"] += votes
        if method != "exact":
            diagnostics["aliased_geographic_votes"] += votes
        for district, weight in allocation:
            allocated = votes * weight
            county_totals[county][district][party] += allocated
            direct_basis[(county, party)][district] += allocated

    for row in pending:
        county = str(row["county"])
        party = str(row["party"])
        votes = int(row["votes"])
        basis = direct_basis.get((county, party)) or {}
        if sum(basis.values()) <= 0:
            basis = county_fallback.get(county) or {}
        if sum(basis.values()) <= 0:
            basis = statewide_fallback
            diagnostics["statewide_fallback_votes"] += votes
        denominator = sum(basis.values())
        if denominator <= 0:
            diagnostics["unallocated_votes"] += votes
            continue
        for district, value in basis.items():
            county_totals[county][district][party] += votes * value / denominator
        diagnostics["imputed_votes"] += votes

    whole_totals, _ = apportion_counties_to_districts(county_totals, county_targets)
    results: dict[str, dict[str, object]] = {}
    for district, values in whole_totals.items():
        dem, rep, other = values["dem"], values["rep"], values["other"]
        total = dem + rep + other
        results[district] = {
            "total_votes": total,
            "dem_votes": dem,
            "rep_votes": rep,
            "other_votes": other,
            "dem_candidate": candidate_names.get("dem", ""),
            "rep_candidate": candidate_names.get("rep", ""),
            "winner": "Democratic" if dem > rep else ("Republican" if rep > dem else "Tie"),
            "winner_party": "DEM" if dem > rep else ("REP" if rep > dem else "TIE"),
            "margin_pct": ((rep - dem) / total * 100) if total else 0,
        }

    geographic_votes = diagnostics["input_votes"] - diagnostics["pseudo_votes"]
    diagnostics["allocated_votes"] = sum(int(row["total_votes"]) for row in results.values())
    diagnostics["matched_precinct_keys"] = len(matched_keys)
    diagnostics["unmatched_precinct_keys"] = len(unmatched_keys)
    diagnostics["county_total_checks"] = len(county_targets) * len(PARTIES)
    diagnostics["direct_vote_coverage_pct"] = (
        diagnostics["direct_geographic_votes"] / diagnostics["input_votes"] * 100
        if diagnostics["input_votes"] else 0
    )
    diagnostics["direct_geographic_vote_coverage_pct"] = (
        diagnostics["direct_geographic_votes"] / geographic_votes * 100 if geographic_votes else 0
    )
    unmatched = [
        {"countyfp": county, "precinct": precinct, "votes": votes}
        for (county, precinct), votes in sorted(unmatched_votes.items())
    ]
    return results, dict(diagnostics), unmatched


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()
    generated_at = datetime.now(timezone.utc).isoformat()
    promoted_entries: list[dict[str, object]] = []

    for year, election in ELECTIONS.items():
        output_dir = DATA / "district_contests_historical_proxy" / str(year)
        output_dir.mkdir(parents=True, exist_ok=True)
        local_manifest = []
        for contest_type, office in election["contests"].items():
            vote_rows, candidates = load_vote_rows(str(election["date"]), office)
            for plan, (scope, lines_year) in PLANS.items():
                results, diagnostics, unmatched = aggregate(vote_rows, candidates, plan)
                filename = f"{plan}_{contest_type}_{year}.json"
                payload = {
                    "meta": {
                        "status": "provisional_historical_proxy",
                        "scope": scope,
                        "contest_type": contest_type,
                        "year": year,
                        "district_lines_year": lines_year,
                        "vtd_vintage": 2008,
                        "generated_at_utc": generated_at,
                        "source": "Maryland SBE precinct returns via 2008 VTD and Census block chain",
                        "crosswalk": f"data/crosswalks_2008_vtd/vtd2008_to_{plan}.csv",
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

                if args.promote and plan != "congressional_2021_mcrc":
                    main_dir = DATA / "district_contests"
                    main_filename = f"{scope}_{contest_type}_{year}.json"
                    (main_dir / main_filename).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                    promoted_entries.append(
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
                            "vtd_vintage": 2008,
                            "allocation_method": "county_constrained_2008_vtd_block_chain_with_county_imputation",
                        }
                    )
                print(
                    f"{year} {contest_type} {plan}: {diagnostics['direct_geographic_vote_coverage_pct']:.2f}% "
                    "direct geographic coverage"
                )
        (output_dir / "manifest.json").write_text(json.dumps({"files": local_manifest}, indent=2) + "\n", encoding="utf-8")

    if args.promote:
        manifest_path = DATA / "district_contests" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        keys = {(row["scope"], row["contest_type"], int(row["year"])) for row in promoted_entries}
        files = [
            row for row in manifest.get("files", [])
            if (row.get("scope"), row.get("contest_type"), int(row.get("year", 0))) not in keys
        ]
        files.extend(promoted_entries)
        files.sort(key=lambda row: (str(row["scope"]), str(row["contest_type"]), int(row["year"])))
        manifest["files"] = files
        manifest["includes_provisional_historical_slices"] = True
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"promoted {len(promoted_entries)} historical slices into data/district_contests")


if __name__ == "__main__":
    main()

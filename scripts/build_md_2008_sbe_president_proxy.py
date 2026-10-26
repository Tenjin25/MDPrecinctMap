"""Proof-of-concept 2008 SBE presidential aggregation through 2008 VTDs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from md_county_constrained import PARTIES, apportion_counties_to_districts


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CROSSWALKS = DATA / "crosswalks_2008_vtd"
OUT = DATA / "district_contests_historical_proxy" / "2008"

COUNTY_FIPS = {
    "ALLEGANY": "001", "ANNEARUNDEL": "003", "BALTIMORE": "005",
    "CALVERT": "009", "CAROLINE": "011", "CARROLL": "013", "CECIL": "015",
    "CHARLES": "017", "DORCHESTER": "019", "FREDERICK": "021", "GARRETT": "023",
    "HARFORD": "025", "HOWARD": "027", "KENT": "029", "MONTGOMERY": "031",
    "PRINCEGEORGES": "033", "QUEENANNES": "035", "SAINTMARYS": "037",
    "SOMERSET": "039", "TALBOT": "041", "WASHINGTON": "043", "WICOMICO": "045",
    "WORCESTER": "047", "BALTIMORECITY": "510",
}

PLANS = {
    "congressional_2022_enacted": ("congressional", 2022),
    "congressional_2021_mcrc": ("congressional", 2021),
    "state_house_2022": ("state_house", 2022),
    "state_senate_2022": ("state_senate", 2022),
}

PSEUDO_PRECINCTS = {"ABSENTEE", "PROVISIONAL", "EARLY VOTING"}


def norm_name(value: str) -> str:
    value = value.upper().replace("ST.", "SAINT").replace(" COUNTY", "")
    return re.sub(r"[^A-Z0-9]", "", value)


def norm_code(value: str) -> str:
    parts = re.findall(r"\d+", value or "")
    return "-".join(str(int(part)) for part in parts[:2]) if len(parts) >= 2 else ""


def party_bucket(party: str) -> str:
    party = (party or "").strip().upper()
    if party == "DEM":
        return "dem"
    if party == "REP":
        return "rep"
    return "other"


def apportion_whole_votes(
    district_totals: dict[str, dict[str, float]],
) -> dict[str, dict[str, int]]:
    output = {district: {"dem": 0, "rep": 0, "other": 0} for district in district_totals}
    for party in ("dem", "rep", "other"):
        raw = {district: max(0.0, float(values.get(party, 0))) for district, values in district_totals.items()}
        target = int(round(sum(raw.values())))
        apportioned = {district: math.floor(value) for district, value in raw.items()}
        remainder = target - sum(apportioned.values())
        order = sorted(raw, key=lambda district: (raw[district] - apportioned[district], district), reverse=True)
        for district in order[:remainder]:
            apportioned[district] += 1
        for district, votes in apportioned.items():
            output[district][party] = votes
    return output


def load_crosswalk(plan: str) -> tuple[dict[tuple[str, str], list[tuple[str, float]]], dict[str, dict[str, float]]]:
    path = CROSSWALKS / f"vtd2008_to_{plan}.csv"
    by_vtd: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    county_fallback: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            county = row["countyfp00"]
            code = norm_code(row["vtd_code"])
            district = row["district_num"]
            weight = float(row["allocation_weight"])
            by_vtd[(county, code)].append((district, weight))
            county_fallback[county][district] += weight
    for district_weights in county_fallback.values():
        total = sum(district_weights.values())
        if total:
            for district in list(district_weights):
                district_weights[district] /= total
    return by_vtd, county_fallback


def resolve_vtd(
    county: str, precinct: str, by_vtd: dict[tuple[str, str], list[tuple[str, float]]]
) -> tuple[list[tuple[str, float]] | None, str]:
    code = norm_code(precinct)
    exact = by_vtd.get((county, code))
    if exact:
        return exact, "exact"
    parts = code.split("-")
    if len(parts) == 2 and parts[1] == "0":
        candidates = [key for key in by_vtd if key[0] == county and key[1].split("-")[0] == parts[0]]
        if len(candidates) == 1:
            return by_vtd[candidates[0]], "single_vtd_district_alias"
    return None, "unmatched"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Also write enacted-plan slices into data/district_contests and merge its manifest.",
    )
    args = parser.parse_args()
    source = DATA / "openelections" / "md_sbe" / "20081104__md__general__precinct.csv"
    vote_rows: list[dict[str, object]] = []
    candidates: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["office"] != "President":
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
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    promoted_entries: list[dict[str, object]] = []
    generated_at = datetime.now(timezone.utc).isoformat()

    for plan, (scope, lines_year) in PLANS.items():
        by_vtd, county_fallback = load_crosswalk(plan)
        county_totals: dict[str, dict[str, dict[str, float]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(float))
        )
        county_targets: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        direct_basis: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
        pending: list[dict[str, object]] = []
        diagnostics = defaultdict(float)
        matched_keys: set[tuple[str, str]] = set()
        unmatched_keys: set[tuple[str, str]] = set()
        unmatched_vote_totals: dict[tuple[str, str], int] = defaultdict(int)

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
                unmatched_vote_totals[key] += votes
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
            basis = direct_basis.get((county, party)) or county_fallback.get(county) or {}
            basis_total = sum(basis.values())
            if basis_total <= 0:
                diagnostics["unallocated_votes"] += votes
                continue
            for district, value in basis.items():
                county_totals[county][district][party] += votes * value / basis_total
            diagnostics["imputed_votes"] += votes

        results = {}
        whole_totals, _ = apportion_counties_to_districts(county_totals, county_targets)
        for district, values in sorted(whole_totals.items(), key=lambda item: (int(re.match(r"\d+", item[0]).group()), item[0])):
            dem = values.get("dem", 0)
            rep = values.get("rep", 0)
            other = values.get("other", 0)
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

        allocated_total = sum(float(row["total_votes"]) for row in results.values())
        diagnostics["allocated_votes"] = allocated_total
        diagnostics["matched_precinct_keys"] = len(matched_keys)
        diagnostics["unmatched_precinct_keys"] = len(unmatched_keys)
        diagnostics["county_total_checks"] = len(county_targets) * len(PARTIES)
        diagnostics["direct_vote_coverage_pct"] = (
            diagnostics["direct_geographic_votes"] / diagnostics["input_votes"] * 100
            if diagnostics["input_votes"] else 0
        )
        geographic_votes = diagnostics["input_votes"] - diagnostics["pseudo_votes"]
        diagnostics["direct_geographic_vote_coverage_pct"] = (
            diagnostics["direct_geographic_votes"] / geographic_votes * 100
            if geographic_votes else 0
        )
        if plan == "congressional_2022_enacted":
            with (OUT / "unmatched_geographic_precincts.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["countyfp", "precinct", "president_votes"])
                writer.writeheader()
                for (county, precinct), votes in sorted(unmatched_vote_totals.items()):
                    writer.writerow({"countyfp": county, "precinct": precinct, "president_votes": votes})
        filename = f"{plan}_president_2008.json"
        payload = {
            "meta": {
                "status": "provisional_historical_proxy",
                "scope": scope,
                "contest_type": "president",
                "year": 2008,
                "district_lines_year": lines_year,
                "generated_at_utc": generated_at,
                "source": "Maryland SBE precinct returns via 2008 VTD and Census block chain",
                "crosswalk": f"data/crosswalks_2008_vtd/vtd2008_to_{plan}.csv",
                "county_constrained_aggregation": True,
                "county_constrained_method": (
                    "Preserve each county/party total exactly; allocate only split-county "
                    "components with VTD/block weights; largest-remainder round within "
                    "each county/party; then sum county contributions into districts."
                ),
                "diagnostics": dict(diagnostics),
            },
            "general": {"results": results},
        }
        (OUT / filename).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        manifest.append({"plan": plan, "scope": scope, "year": 2008, "contest_type": "president", "file": filename, **dict(diagnostics)})
        if args.promote and plan != "congressional_2021_mcrc":
            promoted_dir = DATA / "district_contests"
            promoted_dir.mkdir(parents=True, exist_ok=True)
            promoted_filename = f"{scope}_president_2008.json"
            (promoted_dir / promoted_filename).write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
            promoted_entries.append(
                {
                    "scope": scope,
                    "contest_type": "president",
                    "year": 2008,
                    "file": promoted_filename,
                    "rows": len(results),
                    "districts": len(results),
                    "total_votes": round(sum(float(r["total_votes"]) for r in results.values()), 2),
                    "dem_total": round(sum(float(r["dem_votes"]) for r in results.values()), 2),
                    "rep_total": round(sum(float(r["rep_votes"]) for r in results.values()), 2),
                    "other_total": round(sum(float(r["other_votes"]) for r in results.values()), 2),
                    "major_party_contested": True,
                    "match_coverage_pct": diagnostics["direct_geographic_vote_coverage_pct"],
                    "provisional": True,
                    "allocation_method": "county_constrained_2008_vtd_block_chain_with_county_imputation",
                }
            )
        print(
            f"wrote {filename}: {len(results)} districts; direct vote coverage "
            f"{diagnostics['direct_vote_coverage_pct']:.2f}%"
        )

    (OUT / "manifest.json").write_text(json.dumps({"files": manifest}, indent=2) + "\n", encoding="utf-8")
    if args.promote:
        promoted_manifest_path = DATA / "district_contests" / "manifest.json"
        promoted_manifest = json.loads(promoted_manifest_path.read_text(encoding="utf-8"))
        promoted_keys = {
            (str(row["scope"]), str(row["contest_type"]), int(row["year"]))
            for row in promoted_entries
        }
        files = [
            row for row in promoted_manifest.get("files", [])
            if (str(row.get("scope")), str(row.get("contest_type")), int(row.get("year", 0)))
            not in promoted_keys
        ]
        files.extend(promoted_entries)
        files.sort(key=lambda row: (str(row["scope"]), str(row["contest_type"]), int(row["year"])))
        promoted_manifest["files"] = files
        promoted_manifest["includes_provisional_historical_slices"] = True
        promoted_manifest_path.write_text(
            json.dumps(promoted_manifest, indent=2) + "\n", encoding="utf-8"
        )
        print(f"promoted {len(promoted_entries)} slices into data/district_contests")


if __name__ == "__main__":
    main()

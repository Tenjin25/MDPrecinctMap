"""Build county and canonical-VTD20 contest slices from Maryland SBE precinct CSVs."""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from build_md_bef_crosswalks import DATA, ROOT
from build_md_precinct_assets import canonical_code


OUT = DATA / "contests"
SBE = DATA / "openelections" / "md_sbe"
CONTESTS = {
    "president": ("President", [2000, 2004, 2008, 2012, 2016, 2020, 2024]),
    "us_senate": ("US Senate", [2000, 2004, 2006, 2010, 2012, 2016, 2018, 2022, 2024]),
    "governor": ("Governor", [2002, 2006, 2010, 2014, 2018, 2022]),
    "attorney_general": ("Attorney General", [2006, 2010, 2014, 2018, 2022]),
    "comptroller": ("Comptroller", [2006, 2010, 2014, 2018, 2022]),
}
DATES = {
    2000: "20001107", 2002: "20021105", 2004: "20041102", 2006: "20061107", 2008: "20081104",
    2010: "20101102", 2012: "20121106", 2014: "20141104", 2016: "20161108",
    2018: "20181106", 2020: "20201103", 2022: "20221108", 2024: "20241105",
}


def norm_county(raw: str) -> str:
    value = " ".join(str(raw or "").strip().upper().split())
    if value.endswith(" COUNTY"):
        value = value[:-7]
    if value == "SAINT MARY'S":
        value = "ST. MARY'S"
    return value


def source_key(county: str, precinct: str) -> str:
    code = canonical_code(precinct)
    return f"{norm_county(county)} - {code}" if code else ""


def norm_office(raw: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(raw or "").upper()).strip().replace("U S ", "US ")


def load_weights(path: Path):
    out = defaultdict(list)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            out[row["source"]].append((row["target"], float(row["allocation_weight"])))
    return dict(out)


def load_target_keys():
    payload = json.loads((DATA / "md_vtd_2020.geojson").read_text(encoding="utf-8"))
    keys = {str(f["properties"]["precinct_norm"]).upper() for f in payload["features"]}
    by_county = defaultdict(list)
    for key in keys:
        by_county[key.split(" - ", 1)[0]].append(key)
    return keys, dict(by_county)


def year_weights(year: int, weight_sets, target_keys):
    if year <= 2008:
        return weight_sets["vtd00"]
    if year <= 2018:
        return weight_sets["vtd10"]
    if year >= 2023:
        return weight_sets["current2024"]
    return {key: [(key, 1.0)] for key in target_keys}


def apportion(total: int, targets):
    if total <= 0 or not targets:
        return {}
    weight_sum = sum(weight for _target, weight in targets)
    raw = {target: total * weight / weight_sum for target, weight in targets if weight > 0}
    result = {target: math.floor(value) for target, value in raw.items()}
    remainder = total - sum(result.values())
    order = sorted(raw, key=lambda key: (raw[key] - result[key], key), reverse=True)
    for target in order[:remainder]:
        result[target] += 1
    return result


def result_fields(dem: int, rep: int, other: int, dem_candidate: str, rep_candidate: str):
    total = dem + rep + other
    margin = rep - dem
    margin_pct = (margin / total * 100) if total else 0.0
    winner = "Republican" if rep > dem else ("Democratic" if dem > rep else "Tie")
    return {
        "dem_votes": dem, "rep_votes": rep, "other_votes": other, "total_votes": total,
        "dem_candidate": dem_candidate, "rep_candidate": rep_candidate,
        "margin": margin, "margin_pct": margin_pct, "winner": winner,
    }


def build_slice(contest_type, office, year, weights, target_keys, targets_by_county):
    county_only = year == 2002
    path = (DATA / "openelections" / f"{DATES[year]}__md__general__county.csv") if county_only else (SBE / f"{DATES[year]}__md__general__precinct.csv")
    source_votes = defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0})
    county_votes = defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0})
    county_labels = {}
    candidates = {"dem": Counter(), "rep": Counter()}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if norm_office(row.get("office") or "") != norm_office(office):
                continue
            county = norm_county(row.get("county") or "")
            county_labels[county] = str(row.get("county") or "").strip()
            key = source_key(county, row.get("precinct") or "")
            party_raw = str(row.get("party") or "").strip().upper()
            party = "dem" if party_raw in {"DEM", "DEMOCRATIC"} else ("rep" if party_raw in {"REP", "REPUBLICAN"} else "other")
            votes = int(round(float(row.get("votes") or 0)))
            county_votes[county][party] += votes
            if key:
                source_votes[key][party] += votes
            if party in candidates:
                candidates[party][str(row.get("candidate") or "").strip()] += votes

    dem_candidate = candidates["dem"].most_common(1)[0][0] if candidates["dem"] else ""
    rep_candidate = candidates["rep"].most_common(1)[0][0] if candidates["rep"] else ""
    precinct_votes = defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0})
    unmatched = defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0})
    matched_sources = 0
    for key, votes in source_votes.items():
        targets = weights.get(key)
        if not targets and key in target_keys:
            targets = [(key, 1.0)]
        county = key.split(" - ", 1)[0]
        if not targets:
            for party in ("dem", "rep", "other"):
                unmatched[county][party] += votes[party]
            continue
        matched_sources += 1
        for party in ("dem", "rep", "other"):
            for target, allocated in apportion(votes[party], targets).items():
                precinct_votes[target][party] += allocated

    # Absentee/provisional and unmatched historical codes stay in their county and
    # are apportioned across VTD20 using matched party vote as the basis.
    for county, votes in unmatched.items():
        targets = targets_by_county.get(county, [])
        for party in ("dem", "rep", "other"):
            bases = [(target, float(precinct_votes[target][party])) for target in targets]
            if not sum(weight for _target, weight in bases):
                bases = [(target, 1.0) for target in targets]
            for target, allocated in apportion(votes[party], bases).items():
                precinct_votes[target][party] += allocated

    county_rows = []
    for county, votes in sorted(county_votes.items()):
        county_rows.append({"county": county_labels.get(county, county), **result_fields(
            votes["dem"], votes["rep"], votes["other"], dem_candidate, rep_candidate
        )})
    precinct_rows = []
    for target, votes in sorted(precinct_votes.items()):
        if sum(votes.values()) <= 0:
            continue
        precinct_rows.append({"county": target, **result_fields(
            votes["dem"], votes["rep"], votes["other"], dem_candidate, rep_candidate
        )})

    if not county_only:
        for party in ("dem", "rep", "other"):
            expected = sum(row[f"{party}_votes"] for row in county_rows)
            actual = sum(row[f"{party}_votes"] for row in precinct_rows)
            if actual != expected:
                raise RuntimeError(f"{contest_type}/{year}/{party}: {actual} != {expected}")
    return county_rows, precinct_rows, matched_sources, len(source_votes), county_only, path


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    target_keys, targets_by_county = load_target_keys()
    weight_sets = {
        "vtd00": load_weights(DATA / "crosswalks_vtd20" / "vtd00_to_vtd20.csv"),
        "vtd10": load_weights(DATA / "crosswalks_vtd20" / "vtd10_to_vtd20.csv"),
        "current2024": load_weights(DATA / "crosswalks_vtd20" / "precinct2024_to_vtd20.csv"),
    }
    manifest = []
    for contest_type, (office, years) in CONTESTS.items():
        for year in years:
            weights = year_weights(year, weight_sets, target_keys)
            county_rows, precinct_rows, matched, source_count, county_only, input_path = build_slice(
                contest_type, office, year, weights, target_keys, targets_by_county
            )
            if not county_rows:
                continue
            filename = f"{contest_type}_{year}.json"
            payload = {
                "meta": {
                    "contest_type": contest_type, "year": year,
                    "precinct_geometry": None if county_only else "VTD20", "generated_at_utc": generated_at,
                    "county_only": county_only,
                    "source": str(input_path.relative_to(ROOT)).replace("\\", "/"),
                    "source_precincts": source_count, "directly_matched_source_precincts": matched,
                    "allocation_method": "county_totals_only" if county_only else "whole_vote_largest_remainder_block_crosswalk_with_county_imputation",
                },
                "rows": county_rows,
                "precinct_rows": precinct_rows,
            }
            (OUT / filename).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            manifest.append({
                "contest_type": contest_type, "year": year, "file": filename,
                "rows": len(county_rows), "precinct_rows": len(precinct_rows),
                "county_only": county_only,
                "total_votes": sum(row["total_votes"] for row in county_rows),
            })
            print(f"wrote data/contests/{filename} ({len(county_rows)} counties; {len(precinct_rows)} VTD20 precincts)")
    manifest.sort(key=lambda row: (row["contest_type"], row["year"]))
    (OUT / "manifest.json").write_text(json.dumps({"generated_at_utc": generated_at, "files": manifest}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote data/contests/manifest.json ({len(manifest)} slices)")


if __name__ == "__main__":
    main()

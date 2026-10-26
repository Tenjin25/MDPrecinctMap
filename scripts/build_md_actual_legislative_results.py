"""Build direct district results for Maryland's enacted legislative lines.

U.S. House results are retained only for the enacted congressional districts in
which the elections were held.  The 2022 House of Delegates and State Senate
results are likewise grouped by their reported SBE district, with no geographic
reallocation to another chamber or proposed plan.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone

from build_md_bef_crosswalks import DATA, normalize_district


OUTPUT = DATA / "district_contests"
CONTESTS = [
    (2022, "20221108", "congressional", "us_house", "U.S. House"),
    (2024, "20241105", "congressional", "us_house", "U.S. House"),
    (2022, "20221108", "state_house", "state_house", "House of Delegates"),
    (2022, "20221108", "state_senate", "state_senate", "State Senator"),
]


def party_bucket(value: str) -> str:
    party = (value or "").strip().upper()
    if party == "DEM":
        return "dem"
    if party == "REP":
        return "rep"
    return "other"


def candidate_label(candidate_totals: dict[str, int]) -> str:
    ordered = [
        name for name, votes in sorted(candidate_totals.items(), key=lambda item: (-item[1], item[0]))
        if votes > 0 and "WRITE-IN" not in name.upper()
    ]
    return " / ".join(ordered)


def build_slice(year: int, date: str, scope: str, contest_type: str, office: str):
    source = DATA / "openelections" / "md_sbe" / f"{date}__md__general__precinct.csv"
    totals = defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0})
    candidates = defaultdict(lambda: {"dem": defaultdict(int), "rep": defaultdict(int), "other": defaultdict(int)})
    source_rows = 0
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["office"] != office:
                continue
            district = normalize_district(row["district"], scope)
            if not district:
                continue
            source_rows += 1
            party = party_bucket(row["party"])
            votes = int(row["votes"] or 0)
            totals[district][party] += votes
            candidates[district][party][row["candidate"]] += votes

    results = {}
    for district, values in sorted(totals.items(), key=lambda item: (int(''.join(c for c in item[0] if c.isdigit()) or 0), item[0])):
        dem, rep, other = values["dem"], values["rep"], values["other"]
        total = dem + rep + other
        results[district] = {
            "total_votes": total,
            "dem_votes": dem,
            "rep_votes": rep,
            "other_votes": other,
            "dem_candidate": candidate_label(candidates[district]["dem"]),
            "rep_candidate": candidate_label(candidates[district]["rep"]),
            "winner": "Democratic" if dem > rep else ("Republican" if rep > dem else "Tie"),
            "winner_party": "DEM" if dem > rep else ("REP" if rep > dem else "TIE"),
            "margin_pct": ((rep - dem) / total * 100) if total else 0,
        }

    filename = f"{scope}_{contest_type}_{year}.json"
    payload = {
        "meta": {
            "scope": scope,
            "contest_type": contest_type,
            "year": year,
            "district_lines_year": 2022,
            "source": "direct_maryland_sbe_reported_district_returns",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "match_coverage_pct": 100.0,
            "input_file": f"data/openelections/md_sbe/{source.name}",
            "office": office,
            "source_rows": source_rows,
            "allocation_method": "reported_sbe_district_no_geographic_reallocation",
            "multi_member_note": (
                "House of Delegates totals are candidate votes; multi-member districts can contain multiple candidates per party."
                if scope == "state_house" else None
            ),
        },
        "general": {"results": results},
    }
    (OUTPUT / filename).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    entry = {
        "scope": scope,
        "contest_type": contest_type,
        "year": year,
        "file": filename,
        "rows": len(results),
        "districts": len(results),
        "total_votes": sum(row["total_votes"] for row in results.values()),
        "dem_total": sum(row["dem_votes"] for row in results.values()),
        "rep_total": sum(row["rep_votes"] for row in results.values()),
        "other_total": sum(row["other_votes"] for row in results.values()),
        "major_party_contested": bool(
            sum(row["dem_votes"] for row in results.values())
            and sum(row["rep_votes"] for row in results.values())
        ),
        "match_coverage_pct": 100.0,
        "direct_reported_district_results": True,
    }
    print(f"wrote data/district_contests/{filename} ({len(results)} districts)")
    return entry


def main() -> None:
    manifest_path = OUTPUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # U.S. House is valid only on enacted/current congressional lines.
    files = [
        row for row in manifest.get("files", [])
        if row.get("contest_type") != "us_house"
        and not (
            row.get("contest_type") in {"state_house", "state_senate"}
            and int(row.get("year", 0)) == 2022
        )
    ]
    entries = [build_slice(*spec) for spec in CONTESTS]
    files.extend(entries)
    files.sort(key=lambda row: (str(row["scope"]), str(row["contest_type"]), int(row["year"])))
    manifest["files"] = files
    manifest["actual_legislative_results"] = {
        "us_house": [2022, 2024],
        "state_house": [2022],
        "state_senate": [2022],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # Remove obsolete reallocated U.S. House artifacts outside current congressional lines.
    for path in OUTPUT.glob("*_us_house_*.json"):
        if not path.name.startswith("congressional_"):
            path.unlink()

    mcrc_dir = DATA / "district_contests_2021"
    mcrc_manifest_path = mcrc_dir / "manifest.json"
    if mcrc_manifest_path.exists():
        mcrc_manifest = json.loads(mcrc_manifest_path.read_text(encoding="utf-8"))
        mcrc_manifest["files"] = [
            row for row in mcrc_manifest.get("files", [])
            if row.get("contest_type") != "us_house"
        ]
        mcrc_manifest_path.write_text(json.dumps(mcrc_manifest, indent=2) + "\n", encoding="utf-8")
        for path in mcrc_dir.glob("*_us_house_*.json"):
            path.unlink()


if __name__ == "__main__":
    main()

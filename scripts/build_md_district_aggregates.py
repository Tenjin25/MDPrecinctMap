"""Aggregate RDH block-level Maryland elections into official BEF districts."""

from __future__ import annotations

import json
import csv
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from build_md_bef_crosswalks import DATA, ROOT, read_bef, reader_from_zip
from md_county_constrained import (
    PARTIES,
    apportion_counties_to_districts,
    county_component_catalog,
)


FieldSelector = list[str] | Callable[[str], bool]


ELECTIONS: dict[int, dict[str, object]] = {
    2016: {
        "source": "md_2016_gen_2020_blocks.zip",
        "date": "20161108",
        "contests": {
            "president": {
                "office": "President",
                "dem": ["G16PREDCLI"],
                "rep": ["G16PRERTRU"],
                "other": ["G16PRELJOH", "G16PREGSTE", "G16PREOTH"],
                "candidates": ("Hillary Clinton", "Donald Trump"),
            },
            "us_senate": {
                "office": "U.S. Senate",
                "dem": ["G16USSDVAN"],
                "rep": ["G16USSRSZE"],
                "other": ["G16USSGFLO", "G16USSOTH"],
                "candidates": ("Chris Van Hollen", "Kathy Szeliga"),
            },
        },
    },
    2018: {
        "source": "md_2018_gen_2020_blocks.zip",
        "date": "20181106",
        "contests": {
            "governor": {
                "office": "Governor",
                "dem": ["G18GOVDJEA"],
                "rep": ["G18GOVRHOG"],
                "other": ["G18GOVLQUI", "G18GOVGSCH", "G18GOVOWRI"],
                "candidates": ("Ben Jealous", "Larry Hogan"),
            },
            "comptroller": {
                "office": "Comptroller",
                "dem": ["G18COMDFRA"],
                "rep": ["G18COMRPHU"],
                "other": ["G18COMOWRI"],
                "candidates": ("Peter Franchot", "Anjali Reed Phukan"),
            },
            "attorney_general": {
                "office": "Attorney General",
                "dem": ["G18ATGDFRO"],
                "rep": ["G18ATGRWOL"],
                "other": ["G18ATGOWRI"],
                "candidates": ("Brian Frosh", "Craig Wolf"),
            },
            "us_senate": {
                "office": "U.S. Senate",
                "dem": ["G18USSDCAR"],
                "rep": ["G18USSRCAM"],
                "other": ["G18USSLVOH", "G18USSISIM", "G18USSOWRI"],
                "candidates": ("Ben Cardin", "Tony Campbell"),
            },
        },
    },
    2020: {
        "source": "md_2020_gen_2020_blocks.zip",
        "date": "20201103",
        "contests": {
            "president": {
                "office": "President",
                "dem": ["G20PREDBID"],
                "rep": ["G20PRERTRU"],
                "other": ["G20PRELJOR", "G20PREGHAW", "G20PREBSEG", "G20PREOWRI"],
                "candidates": ("Joe Biden", "Donald Trump"),
            },
        },
    },
    2024: {
        "source": "md_2024_gen_2020_blocks.zip",
        "date": "20241105",
        "contests": {
            "president": {
                "office": "President",
                "dem": ["G24PREDHAR"],
                "rep": ["G24PRERTRU"],
                "other": [
                    "G24PREGSTE", "G24PREOCRU", "G24PREOWRI", "G24PREUKEN",
                    "G24PREUSON", "G24PREUWES",
                ],
                "candidates": ("Kamala Harris", "Donald Trump"),
            },
            "us_senate": {
                "office": "U.S. Senate",
                "dem": ["G24USSDALS"],
                "rep": ["G24USSRHOG"],
                "other": ["G24USSLSCO", "G24USSOBUR", "G24USSOWRI"],
                "candidates": ("Angela Alsobrooks", "Larry Hogan"),
            },
        },
    },
}


COUNTY_FIPS = {
    "ALLEGANY": "001", "ANNEARUNDEL": "003", "BALTIMORE": "005",
    "CALVERT": "009", "CAROLINE": "011", "CARROLL": "013", "CECIL": "015",
    "CHARLES": "017", "DORCHESTER": "019", "FREDERICK": "021", "GARRETT": "023",
    "HARFORD": "025", "HOWARD": "027", "KENT": "029", "MONTGOMERY": "031",
    "PRINCEGEORGES": "033", "QUEENANNES": "035", "SAINTMARYS": "037",
    "SOMERSET": "039", "TALBOT": "041", "WASHINGTON": "043", "WICOMICO": "045",
    "WORCESTER": "047", "BALTIMORECITY": "510",
}
COUNTY_NAMES = {fips: name for name, fips in COUNTY_FIPS.items()}


PLANS = {
    "congressional_enacted": {
        "scope": "congressional",
        "lines_year": 2022,
        "source": "SB1012-2022-Md-Congress-BEQ.xlsx",
        "output": "district_contests",
    },
    "congressional_mcrc": {
        "scope": "congressional",
        "lines_year": 2021,
        "source": "MCRC-2021-Final-Congressional-BEF.xlsx",
        "output": "district_contests_2021",
    },
    "state_house": {
        "scope": "state_house",
        "lines_year": 2022,
        "source": "MD-2022OFFICIALLYADOPTEDHOUSE.xlsx",
        "output": "district_contests",
    },
    "state_senate": {
        "scope": "state_senate",
        "lines_year": 2022,
        "source": "MD-2022OFFICIALLYADOPTEDSENATE.xlsx",
        "output": "district_contests",
    },
}


def resolve_fields(selector: FieldSelector, available: list[str]) -> list[str]:
    if callable(selector):
        return [field for field in available if selector(field)]
    missing = sorted(set(selector) - set(available))
    if missing:
        raise ValueError(f"Missing election columns: {missing}")
    return selector


def clean_number(value: float) -> int | float:
    rounded = round(float(value), 2)
    if abs(rounded - round(rounded)) < 1e-9:
        return int(round(rounded))
    return rounded


def normalize_county_name(value: str) -> str:
    value = value.upper().replace("ST.", "SAINT").replace(" COUNTY", "")
    return re.sub(r"[^A-Z0-9]", "", value)


def party_bucket(value: str) -> str:
    party = (value or "").strip().upper()
    return "dem" if party == "DEM" else ("rep" if party == "REP" else "other")


def load_official_county_targets(
    date: str,
    contest_specs: dict[str, dict[str, object]],
) -> dict[str, dict[str, dict[str, int]]]:
    """Load canonical SBE county/contest/party totals for constrained allocation."""
    office_to_contest = {str(spec["office"]): contest for contest, spec in contest_specs.items()}
    targets = {
        contest: defaultdict(lambda: {party: 0 for party in PARTIES})
        for contest in contest_specs
    }
    path = DATA / "openelections" / "md_sbe" / f"{date}__md__general__county.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            contest = office_to_contest.get(str(row.get("office") or ""))
            if contest is None:
                continue
            county = COUNTY_FIPS.get(normalize_county_name(str(row.get("county") or "")))
            if county is None:
                raise ValueError(f"Unknown county in {path.name}: {row.get('county')!r}")
            targets[contest][county][party_bucket(str(row.get("party") or ""))] += int(
                float(row.get("votes") or 0)
            )
    output = {contest: dict(rows) for contest, rows in targets.items()}
    for contest, rows in output.items():
        missing = sorted(set(COUNTY_NAMES) - set(rows))
        if missing:
            raise ValueError(f"{path.name}/{contest}: missing county totals for {missing}")
    return output


def result_row(values: dict[str, float], candidates: tuple[str, str]) -> dict[str, object]:
    dem = clean_number(values["dem"])
    rep = clean_number(values["rep"])
    other = clean_number(values["other"])
    total = clean_number(float(dem) + float(rep) + float(other))
    margin = ((float(rep) - float(dem)) / float(total) * 100) if total else 0.0
    winner_party = "REP" if rep > dem else ("DEM" if dem > rep else "TIE")
    winner = "Republican" if winner_party == "REP" else ("Democratic" if winner_party == "DEM" else "Tie")
    return {
        "total_votes": total,
        "dem_votes": dem,
        "rep_votes": rep,
        "other_votes": other,
        "dem_candidate": candidates[0],
        "rep_candidate": candidates[1],
        "winner": winner,
        "winner_party": winner_party,
        "margin_pct": margin,
    }


def canonical_candidate_names(
    contest_type: str,
    year: int,
    fallback: tuple[str, str],
) -> tuple[str, str]:
    """Use county contest labels as the display-name authority when available."""
    path = DATA / "contests" / f"{contest_type}_{year}.json"
    if not path.exists():
        return fallback
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    dem = next((str(row.get("dem_candidate") or "").strip() for row in rows if row.get("dem_candidate")), "")
    rep = next((str(row.get("rep_candidate") or "").strip() for row in rows if row.get("rep_candidate")), "")
    return dem or fallback[0], rep or fallback[1]


def main() -> None:
    generated_at = datetime.now(timezone.utc).isoformat()
    assignments = {
        name: read_bef(DATA / str(plan["source"]), str(plan["scope"]))
        for name, plan in PLANS.items()
    }
    manifests: dict[str, list[dict[str, object]]] = defaultdict(list)

    for year, election in ELECTIONS.items():
        source_name = str(election["source"])
        reader = reader_from_zip(DATA / source_name)
        available_fields = [field[0] for field in reader.fields[1:]]
        contest_specs = dict(election["contests"])
        resolved: dict[str, dict[str, object]] = {}
        for contest_type, raw_spec in contest_specs.items():
            spec = dict(raw_spec)
            resolved[contest_type] = {
                "dem": resolve_fields(spec["dem"], available_fields),
                "rep": resolve_fields(spec["rep"], available_fields),
                "other": resolve_fields(spec["other"], available_fields),
                "candidates": spec["candidates"],
                "office": spec["office"],
            }

        official_targets = load_official_county_targets(str(election["date"]), resolved)
        county_totals: dict[
            str, dict[str, dict[str, dict[str, dict[str, float]]]]
        ] = {
            plan_name: {
                contest_type: defaultdict(
                    lambda: defaultdict(lambda: {party: 0.0 for party in PARTIES})
                )
                for contest_type in resolved
            }
            for plan_name in PLANS
        }
        matched_blocks = {plan_name: 0 for plan_name in PLANS}
        input_blocks = 0

        for record in reader.iterRecords():
            row = record.as_dict()
            block = str(row.get("GEOID20") or "").strip()
            if not block:
                continue
            county = str(row.get("COUNTYFP") or "").strip().zfill(3)
            if county not in COUNTY_NAMES:
                raise ValueError(f"{source_name}: unknown COUNTYFP {county!r} for block {block}")
            input_blocks += 1
            block_votes: dict[str, dict[str, float]] = {}
            for contest_type, spec in resolved.items():
                party_votes = {
                    party: sum(float(row.get(field) or 0) for field in spec[party])
                    for party in ("dem", "rep", "other")
                }
                block_votes[contest_type] = party_votes

            for plan_name, district_by_block in assignments.items():
                district = district_by_block.get(block)
                if not district:
                    continue
                matched_blocks[plan_name] += 1
                for contest_type, party_votes in block_votes.items():
                    values = county_totals[plan_name][contest_type][county][district]
                    for party, votes in party_votes.items():
                        values[party] += votes
        reader.close()

        for plan_name, plan in PLANS.items():
            if matched_blocks[plan_name] != input_blocks:
                raise RuntimeError(
                    f"{source_name}/{plan_name}: matched {matched_blocks[plan_name]}/{input_blocks} blocks"
                )
            scope = str(plan["scope"])
            lines_year = int(plan["lines_year"])
            output_dir_name = str(plan["output"])
            output_dir = DATA / output_dir_name
            output_dir.mkdir(parents=True, exist_ok=True)

            for contest_type, raw_county_totals in county_totals[plan_name].items():
                candidates = canonical_candidate_names(
                    contest_type,
                    year,
                    tuple(resolved[contest_type]["candidates"]),
                )
                whole_district_totals, county_contributions = apportion_counties_to_districts(
                    raw_county_totals,
                    official_targets[contest_type],
                )
                results = {
                    district: result_row(values, candidates)
                    for district, values in sorted(
                        whole_district_totals.items(),
                        key=lambda item: (int(re.match(r"\d+", item[0]).group()), item[0]),
                    )
                    if sum(values.values()) > 0
                }
                district_sum = {
                    party: sum(float(row[f"{party}_votes"]) for row in results.values())
                    for party in ("dem", "rep", "other")
                }
                expected = {
                    party: sum(row[party] for row in official_targets[contest_type].values())
                    for party in PARTIES
                }
                for party in PARTIES:
                    if district_sum[party] != expected[party]:
                        raise RuntimeError(
                            f"{plan_name}/{contest_type}/{year}: {party} votes do not conserve "
                            f"({district_sum[party]} vs {expected[party]})"
                        )

                components = county_component_catalog(raw_county_totals)
                named_components = {
                    key: {
                        district: [COUNTY_NAMES.get(county, county) for county in counties]
                        for district, counties in rows.items()
                    }
                    for key, rows in components.items()
                }
                filename = f"{scope}_{contest_type}_{year}.json"
                payload = {
                    "meta": {
                        "scope": scope,
                        "contest_type": contest_type,
                        "year": year,
                        "district_lines_year": lines_year,
                        "source": "rdh_2020_blocks_joined_to_official_maryland_bef",
                        "generated_at_utc": generated_at,
                        "match_coverage_pct": 100.0,
                        "input_blocks": input_blocks,
                        "matched_blocks": matched_blocks[plan_name],
                        "input_file": f"data/{source_name}",
                        "bef_file": f"data/{plan['source']}",
                        "county_total_file": (
                            f"data/openelections/md_sbe/{election['date']}__md__general__county.csv"
                        ),
                        "county_constrained_aggregation": True,
                        "county_constrained_method": (
                            "Reconcile each county/contest/party to its canonical SBE total; "
                            "retain block-derived shares only within split counties; largest-"
                            "remainder round within each county/party; then sum county "
                            "contributions into districts."
                        ),
                        **named_components,
                    },
                    "general": {"results": results},
                }
                (output_dir / filename).write_text(
                    json.dumps(payload, indent=2) + "\n", encoding="utf-8"
                )
                manifest_row = {
                    "scope": scope,
                    "contest_type": contest_type,
                    "year": year,
                    "file": filename,
                    "rows": len(results),
                    "districts": len(results),
                    "total_votes": clean_number(sum(float(r["total_votes"]) for r in results.values())),
                    "dem_total": clean_number(district_sum["dem"]),
                    "rep_total": clean_number(district_sum["rep"]),
                    "other_total": clean_number(district_sum["other"]),
                    "major_party_contested": bool(district_sum["dem"] and district_sum["rep"]),
                    "match_coverage_pct": 100.0,
                    "county_constrained_aggregation": True,
                }
                manifests[output_dir_name].append(manifest_row)
                print(f"wrote data/{output_dir_name}/{filename} ({len(results)} districts)")

    for output_dir_name, files in manifests.items():
        lines_year = 2021 if output_dir_name.endswith("2021") else 2022
        # Direct U.S. House and enacted legislative returns are produced by
        # build_md_actual_legislative_results.py. Preserve those manifest rows
        # when this broader district aggregation is rebuilt later, otherwise
        # the JSON slices remain on disk but disappear from the atlas selector.
        manifest_path = DATA / output_dir_name / "manifest.json"
        if output_dir_name == "district_contests" and manifest_path.exists():
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            generated_keys = {
                (str(row["scope"]), str(row["contest_type"]), int(row["year"]))
                for row in files
            }
            for row in existing_manifest.get("files", []):
                contest_type = str(row.get("contest_type", ""))
                key = (str(row.get("scope", "")), contest_type, int(row.get("year", 0)))
                slice_path = DATA / output_dir_name / str(row.get("file", ""))
                if (
                    contest_type in {"us_house", "state_house", "state_senate"}
                    and key not in generated_keys
                    and slice_path.is_file()
                ):
                    files.append(row)
                    generated_keys.add(key)
        files.sort(key=lambda row: (str(row["scope"]), str(row["contest_type"]), int(row["year"])))
        manifest = {
            "source": "RDH Maryland election results on 2020 Census blocks + official Maryland BEFs",
            "district_lines_year": lines_year,
            "generated_at_utc": generated_at,
            "files": files,
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote data/{output_dir_name}/manifest.json ({len(files)} slices)")


if __name__ == "__main__":
    main()

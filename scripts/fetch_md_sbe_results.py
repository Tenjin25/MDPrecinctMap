#!/usr/bin/env python3
"""Download Maryland SBE general-election results and emit OpenElections CSVs.

The SBE did not report every vote mode by precinct before 2020.  For those
years, real precinct rows contain election-night votes and countywide canvass
batches are retained as clearly labelled pseudo-precincts (ABSENTEE,
PROVISIONAL, etc.).  This keeps county/state totals complete without inventing
geographic allocations.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path


BASE = "https://elections.maryland.gov/elections/archive/{year}/election_data/"
DEFAULT_YEARS = (2000, 2004, 2006, 2008, 2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024)
ELECTION_DATES = {
    2000: "20001107", 2004: "20041102", 2006: "20061107",
    2008: "20081104", 2010: "20101102", 2012: "20121106",
    2014: "20141104", 2016: "20161108", 2018: "20181106",
    2020: "20201103", 2022: "20221108", 2024: "20241105",
}
COUNTIES = {
    "01": "Allegany", "02": "Anne Arundel", "03": "Baltimore City",
    "04": "Baltimore", "05": "Calvert", "06": "Caroline",
    "07": "Carroll", "08": "Cecil", "09": "Charles",
    "10": "Dorchester", "11": "Frederick", "12": "Garrett",
    "13": "Harford", "14": "Howard", "15": "Kent",
    "16": "Montgomery", "17": "Prince George's", "18": "Queen Anne's",
    "19": "St. Mary's", "20": "Somerset", "21": "Talbot",
    "22": "Washington", "23": "Wicomico", "24": "Worcester",
}
FIELDS = ("county", "precinct", "office", "district", "party", "candidate", "votes", "winner")


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


def fetch(url: str, attempts: int = 5) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "MDPrecinctMap/1.0 (public election data)"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError):
            if attempt + 1 == attempts:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Unable to fetch {url}")


def decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def csv_rows(url: str) -> list[dict[str, str]]:
    text = decode(fetch(url)).replace("\x00", "")
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for raw in reader:
        row = {}
        for key, value in raw.items():
            if key is None:
                continue
            row[key.strip().strip('"').lstrip("\ufeff")] = (value or "").strip()
        if any(row.values()):
            out.append(row)
    return out


def number(value: str | None) -> int:
    raw = (value or "").replace(",", "").strip()
    if not raw:
        return 0
    try:
        return int(round(float(raw)))
    except ValueError:
        return 0


def first(row: dict[str, str], *names: str) -> str:
    for name in names:
        if name in row and row[name] != "":
            return row[name].strip()
    return ""


def county_name(row: dict[str, str]) -> str:
    code = first(row, "County").zfill(2)
    supplied = first(row, "County Name")
    if supplied:
        supplied = re.sub(r"\s+County$", "", supplied, flags=re.I).strip()
        if supplied.lower() == "baltimore city":
            return "Baltimore City"
        return supplied
    return COUNTIES.get(code, code)


def county_name_from_url(url: str) -> str:
    filename = Path(urllib.parse.urlparse(url).path).name
    token = re.sub(r"[^a-z0-9]", "", filename.lower())
    aliases = {
        "Prince George's": ("princegeorges",),
        "Queen Anne's": ("queenannes",),
        "St. Mary's": ("stmarys", "saintmarys"),
    }
    for name in sorted(COUNTIES.values(), key=len, reverse=True):
        candidates = aliases.get(name, (re.sub(r"[^a-z0-9]", "", name.lower()),))
        if any(token.startswith(candidate) for candidate in candidates):
            return name
    return ""


def precinct_name(row: dict[str, str]) -> str:
    combined = first(row, "Election District - Precinct")
    if combined:
        return combined
    district = first(row, "Election District")
    precinct = first(row, "Election Precinct")
    return f"{district}-{precinct}".strip("-")


def normalize_office(value: str) -> str:
    raw = re.sub(r"\s+", " ", value).strip()
    norm = re.sub(r"[^A-Z0-9]+", " ", raw.upper()).strip()
    if norm.startswith("PRESIDENT") and "CITY COUNCIL" not in norm:
        return "President"
    if norm in {"U S SENATOR", "US SENATOR", "U S SENATE", "US SENATE"}:
        return "U.S. Senate"
    if (
        "REPRESENTATIVE IN CONGRESS" in norm
        or "REP IN CONGRESS" in norm
        or norm in {"U S CONGRESS", "US CONGRESS"}
        or norm.startswith("U S HOUSE")
    ):
        return "U.S. House"
    if norm.startswith("GOVERNOR"):
        return "Governor"
    if norm == "ATTORNEY GENERAL":
        return "Attorney General"
    return raw


def winner(value: str) -> str:
    return "TRUE" if value.strip().upper() in {"Y", "YES", "TRUE", "1"} else ""


def precinct_votes(row: dict[str, str]) -> int:
    # 2020 includes component columns plus Total Votes; using Total alone avoids
    # the exact 2x double count found in the old converted file.
    if "Total Votes" in row and row["Total Votes"] != "":
        return number(row["Total Votes"])
    vote_fields = []
    for key in row:
        key_norm = key.lower()
        if "against" in key_norm or "total" in key_norm:
            continue
        if key_norm.endswith(" votes") or key_norm == "votes":
            vote_fields.append(key)
    return sum(number(row.get(key)) for key in vote_fields)


def row_key(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(row[field] for field in FIELDS if field != "votes")


def normalized_row(source: dict[str, str], precinct: str, votes: int) -> dict[str, str]:
    return {
        "county": county_name(source),
        "precinct": precinct,
        "office": normalize_office(first(source, "Office Name", "Office")),
        "district": first(source, "Office District", "District"),
        "party": first(source, "Party"),
        "candidate": first(source, "Candidate Name", "Candidate"),
        "votes": str(votes),
        "winner": winner(first(source, "Winner")),
    }


def canvass_columns(row: dict[str, str]) -> list[tuple[str, str]]:
    labels = []
    for key in row:
        norm = key.lower()
        if "against" in norm or "total" in norm or "election night" in norm or "election day" in norm:
            continue
        if not (norm.endswith(" votes") or norm == "votes"):
            continue
        if "early" in norm:
            label = "EARLY VOTING"
        elif "provisional" in norm or norm.startswith("prov"):
            label = "PROVISIONAL"
        elif "3rd" in norm or "third" in norm:
            label = "3RD ABSENTEE"
        elif "2nd" in norm or "second" in norm or "mail-in ballot 2" in norm:
            label = "2ND ABSENTEE"
        elif "absentee" in norm or "mail" in norm:
            label = "ABSENTEE"
        else:
            continue
        labels.append((key, label))
    return labels


def select_links(year: int) -> tuple[list[str], list[str], str]:
    base = BASE.format(year=year)
    parser = LinkParser()
    parser.feed(decode(fetch(urllib.parse.urljoin(base, "index.html"))))
    links = [urllib.parse.urljoin(base, href) for href in parser.links if href.lower().endswith(".csv")]

    if year in {2012, 2014, 2016, 2018, 2020}:
        wanted = f"All_By_Precinct_{year}_General.csv".lower()
        precinct = [u for u in links if Path(urllib.parse.urlparse(u).path).name.lower() == wanted]
    elif year == 2022:
        precinct = [u for u in links if Path(urllib.parse.urlparse(u).path).name.lower() == "gg22_allprecincts.csv"]
    elif year == 2024:
        precinct = [u for u in links if Path(urllib.parse.urlparse(u).path).name.lower() == "pg24_allprecincts.csv"]
    else:
        precinct = [u for u in links if re.search(rf"(?i)_By_Precinct_(?:General_{year}|{year}_General)\.csv$", u)]
        precinct = [u for u in precinct if "question" not in u.lower() and "state_precinct_reference" not in u.lower()]

    county: list[str] = []
    if year < 2020:
        for url in links:
            name = Path(urllib.parse.urlparse(url).path).name
            low = name.lower()
            if any(token in low for token in ("question", "precinct", "legislative", "congressional", "counc")):
                continue
            if year in {2000, 2004} and re.search(rf"(?i)_General_{year}\.csv$", name):
                county.append(url)
            elif year >= 2006 and re.search(rf"(?i)_County_{year}_General\.csv$", name):
                county.append(url)

    if not precinct:
        raise RuntimeError(f"No official precinct CSV links found for {year}")
    mode = "all vote modes by geographic precinct" if year >= 2020 else "election-night geographic precincts plus countywide canvass pseudo-precincts"
    return sorted(set(precinct)), sorted(set(county)), mode


def merge_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: dict[tuple[str, ...], int] = defaultdict(int)
    samples: dict[tuple[str, ...], dict[str, str]] = {}
    for row in rows:
        key = row_key(row)
        merged[key] += number(row["votes"])
        samples[key] = row
    out = []
    for key, votes in merged.items():
        row = samples[key].copy()
        row["votes"] = str(votes)
        out.append(row)
    return sorted(out, key=lambda r: (r["county"], r["precinct"], r["office"], r["district"], r["party"], r["candidate"]))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def convert_year(year: int, output_dir: Path) -> dict[str, object]:
    precinct_urls, county_urls, coverage = select_links(year)
    rows: list[dict[str, str]] = []
    for url in precinct_urls:
        for source in csv_rows(url):
            row = normalized_row(source, precinct_name(source), precinct_votes(source))
            if row["county"] and row["precinct"] and row["office"] and row["candidate"]:
                rows.append(row)

    # Before 2020, SBE precinct files are Election Day only. Preserve the
    # countywide canvass modes under explicit pseudo-precinct labels.
    for url in county_urls:
        source_county = county_name_from_url(url)
        for source in csv_rows(url):
            if source_county and not first(source, "County", "County Name"):
                source["County Name"] = source_county
            for column, label in canvass_columns(source):
                votes = number(source.get(column))
                if votes:
                    rows.append(normalized_row(source, label, votes))

    precinct_rows = merge_rows(rows)
    county_seed = []
    for row in precinct_rows:
        item = row.copy()
        item["precinct"] = ""
        county_seed.append(item)
    county_rows = merge_rows(county_seed)

    date = ELECTION_DATES[year]
    precinct_path = output_dir / f"{date}__md__general__precinct.csv"
    county_path = output_dir / f"{date}__md__general__county.csv"
    write_csv(precinct_path, precinct_rows)
    write_csv(county_path, county_rows)
    return {
        "year": year,
        "date": date,
        "coverage": coverage,
        "precinct_rows": len(precinct_rows),
        "county_rows": len(county_rows),
        "precincts": len({(r["county"], r["precinct"]) for r in precinct_rows if r["precinct"]}),
        "precinct_file": precinct_path.name,
        "county_file": county_path.name,
        "source_urls": precinct_urls + county_urls,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", nargs="+", type=int, default=list(DEFAULT_YEARS))
    parser.add_argument("--output-dir", type=Path, default=Path("data/openelections/md_sbe"))
    args = parser.parse_args()
    unknown = sorted(set(args.years) - set(ELECTION_DATES))
    if unknown:
        raise SystemExit(f"Unsupported years: {unknown}")

    manifest_by_year: dict[int, dict[str, object]] = {}
    manifest_path = args.output_dir / "manifest.json"
    if manifest_path.exists():
        try:
            prior = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_by_year = {int(item["year"]): item for item in prior.get("elections", [])}
        except (OSError, ValueError, KeyError, TypeError):
            manifest_by_year = {}
    for year in args.years:
        print(f"Converting {year}...", flush=True)
        manifest_by_year[year] = convert_year(year, args.output_dir)
    manifest = [manifest_by_year[year] for year in sorted(manifest_by_year)]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"elections": manifest}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "elections": manifest}, indent=2))


if __name__ == "__main__":
    main()

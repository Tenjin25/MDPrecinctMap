#!/usr/bin/env python3
"""Validate unified Maryland OpenElections CSVs and their county rollups."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path


FIELDS = ["county", "precinct", "office", "district", "party", "candidate", "votes", "winner"]
BASE = Path("data/openelections/md_sbe")


def read(path: Path) -> tuple[list[dict[str, str]], int]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDS:
            raise AssertionError(f"{path}: unexpected header {reader.fieldnames}")
        rows = list(reader)
    negative_corrections = 0
    for line, row in enumerate(rows, start=2):
        if not row["county"] or not row["office"] or not row["candidate"]:
            raise AssertionError(f"{path}:{line}: missing required value")
        if int(row["votes"]) < 0:
            # A handful of official 2010 rows are signed canvass corrections.
            negative_corrections += 1
    return rows, negative_corrections


def grouped(rows: list[dict[str, str]]) -> dict[tuple[str, ...], int]:
    out: dict[tuple[str, ...], int] = defaultdict(int)
    for row in rows:
        key = (row["county"], row["office"], row["district"], row["party"], row["candidate"], row["winner"])
        out[key] += int(row["votes"])
    return dict(out)


def main() -> None:
    manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))["elections"]
    if len(manifest) != 12:
        raise AssertionError(f"Expected 12 elections, found {len(manifest)}")
    for item in manifest:
        precinct_path = BASE / item["precinct_file"]
        county_path = BASE / item["county_file"]
        precinct_rows, precinct_corrections = read(precinct_path)
        county_rows, county_corrections = read(county_path)
        if grouped(precinct_rows) != grouped(county_rows):
            raise AssertionError(f"{item['year']}: county file does not equal precinct rollup")
        counties = {row["county"] for row in precinct_rows}
        if len(counties) != 24:
            raise AssertionError(f"{item['year']}: expected 24 counties/localities, found {len(counties)}")
        correction_note = f"; {precinct_corrections} signed source corrections" if precinct_corrections else ""
        if county_corrections > precinct_corrections:
            correction_note += f"; {county_corrections} county corrections"
        print(f"{item['year']}: {len(precinct_rows):,} precinct rows; {len(county_rows):,} county rows{correction_note}; OK")


if __name__ == "__main__":
    main()

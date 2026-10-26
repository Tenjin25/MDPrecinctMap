#!/usr/bin/env python3
"""OCR Baltimore County's scanned 2002 canvass into OpenElections rows.

The first implementation extracts the Governor/Lt. Governor table (PDF pages
12-16), the statewide contest most useful to the atlas. It combines those
precinct rows with the existing county-level statewide file for jurisdictions
whose precinct canvasses have not yet been recovered.
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageOps


FIELDS = ["county", "precinct", "office", "district", "party", "candidate", "votes", "winner"]
EXPECTED = [106195, 170920, 1972, 300]
CANDIDATES = [
    ("DEM", "Kathleen Kennedy Townsend and Charles R. Larson", ""),
    ("REP", "Robert L. Ehrlich, Jr. and Michael S. Steele", "TRUE"),
    ("LIB", "Spear Lancaster and Lorenzo Gaztanaga", ""),
    ("WRI", "Write-In Total", ""),
]
PAGES = range(12, 17)
COLUMN_BOXES = {
    12: [(690, 800), (805, 920), (925, 1030), (1035, 1130)],
    13: [(845, 950), (950, 1070), (1070, 1180), (1180, 1275)],
    14: [(695, 790), (790, 900), (910, 1020), (1020, 1120)],
    15: [(840, 940), (940, 1050), (1065, 1175), (1175, 1280)],
    16: [(700, 800), (800, 910), (910, 1025), (1025, 1120)],
}


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def code_token(value: str) -> str | None:
    if not re.fullmatch(r"[0-9OoilILS$]{4}", value):
        return None
    table = str.maketrans({"O": "0", "o": "0", "I": "1", "i": "1", "l": "1", "L": "1", "S": "5", "$": "9"})
    code = value.translate(table)
    return code if code.isdigit() else None


def normalize_number(value: str) -> int:
    raw = value.strip().replace("\x0c", "")
    translations = {
        "O": "0", "o": "0", "Q": "0", "C": "0", "D": "0", "a": "0", "@": "0",
        "I": "1", "i": "1", "l": "1", "L": "1", "|": "1", "!": "1",
        "S": "5", "s": "5", "B": "8", "g": "8", "&": "6",
    }
    cleaned = "".join(translations.get(char, char) for char in raw)
    cleaned = re.sub(r"[^0-9]", "", cleaned)
    if not cleaned:
        return 0
    # The italic typeface makes a terminal 2 look like Z, while an internal
    # second 1 is occasionally read as Z (e.g. 1139 -> IZ39). Resolve the
    # latter only in 4-digit vote totals.
    if len(cleaned) == 4 and raw.upper().startswith(("IZ", "LZ")):
        cleaned = "11" + cleaned[2:]
    return int(cleaned)


def valid_2004_codes(path: Path) -> set[str]:
    codes = set()
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["county"] != "Baltimore" or not re.fullmatch(r"\d{3}-\d{3}", row["precinct"]):
                continue
            district, precinct = row["precinct"].split("-")
            codes.add(f"{int(district):02d}{int(precinct):02d}")
    codes.update(f"98{i:02d}" for i in range(1, 9))
    codes.update(f"99{i:02d}" for i in range(1, 9))
    return codes


def render_and_locate_rows(pdf: Path, work: Path, pdftoppm: str, tesseract: str, tessdata: Path, valid: set[str]) -> list[tuple[int, str, int, Path]]:
    found = []
    last_code = -1
    for page in PAGES:
        stem = work / f"page-{page:03d}"
        png = stem.with_suffix(".png")
        tsv = stem.with_suffix(".tsv")
        run([pdftoppm, "-f", str(page), "-l", str(page), "-singlefile", "-png", "-gray", "-r", "300", str(pdf), str(stem)])
        run([tesseract, str(png), str(stem), "--tessdata-dir", str(tessdata), "-l", "eng", "--psm", "6", "tsv"])
        records = []
        for line in tsv.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) != 12:
                continue
            try:
                x, y = int(parts[6]), int(parts[7])
            except ValueError:
                continue
            code = code_token(parts[11].strip())
            if x > 350 or y < 900 or not code or code not in valid:
                continue
            numeric = int(code)
            if numeric <= last_code:
                continue
            found.append((page, code, y, png))
            last_code = numeric
    return found


def ocr_cells(rows: list[tuple[int, str, int, Path]], work: Path, tesseract: str, tessdata: Path) -> list[list[int]]:
    cell_dir = work / "cells"
    cell_dir.mkdir()
    images = []
    for row_index, (page, _code, y, png) in enumerate(rows):
        image = Image.open(png).convert("L")
        for column, (left, right) in enumerate(COLUMN_BOXES[page]):
            box = (left, y - 8, right, y + 34)
            cell = ImageOps.autocontrast(image.crop(box)).resize(((right - left) * 4, 168))
            path = cell_dir / f"{row_index:03d}-{column}.png"
            cell.save(path)
            images.append(path)
    list_path = work / "cells.txt"
    list_path.write_text("\n".join(str(path) for path in images), encoding="utf-8")
    output_stem = work / "cell-ocr"
    run([tesseract, str(list_path), str(output_stem), "--tessdata-dir", str(tessdata), "-l", "eng", "--psm", "7"])
    pages = output_stem.with_suffix(".txt").read_text(encoding="utf-8", errors="ignore").split("\x0c")
    values = [normalize_number(page) for page in pages if page.strip()]
    if len(values) != len(images):
        raise RuntimeError(f"OCR returned {len(values)} cells for {len(images)} images")
    return [values[index:index + 4] for index in range(0, len(values), 4)]


def format_precinct(code: str) -> str:
    if code.startswith("98"):
        return f"ABSENTEE-{code[2:]}"
    if code.startswith("99"):
        return f"PROVISIONAL-OVERSEAS-{code[2:]}"
    return f"0{int(code[:2]):02d}-0{int(code[2:]):02d}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, default=Path("data/BalCounty2002generalresults.pdf"))
    parser.add_argument("--county-input", type=Path, default=Path("data/openelections/20021105__md__general__county.csv"))
    parser.add_argument("--reference-2004", type=Path, default=Path("data/openelections/md_sbe/20041102__md__general__precinct.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/openelections/md_sbe"))
    parser.add_argument("--pdftoppm", default="pdftoppm")
    parser.add_argument("--tesseract", type=Path, default=Path("tmp/tesseract/tesseract.exe"))
    parser.add_argument("--tessdata", type=Path, default=Path("tmp/tesseract/tessdata"))
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="md2002_ocr_", dir="tmp") as temp:
        work = Path(temp)
        rows = render_and_locate_rows(args.pdf, work, args.pdftoppm, str(args.tesseract), args.tessdata, valid_2004_codes(args.reference_2004))
        values = ocr_cells(rows, work, str(args.tesseract), args.tessdata)

    totals = [sum(v[index] for v in values) for index in range(4)]
    if totals != EXPECTED:
        raise RuntimeError(f"OCR totals failed reconciliation: got {totals}, expected {EXPECTED}")

    output = []
    with args.county_input.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["county"] == "Baltimore" and row["office"] == "Governor":
                continue
            output.append(row)
    for (_page, code, _y, _png), vote_values in zip(rows, values):
        for column, votes in enumerate(vote_values):
            party, candidate, won = CANDIDATES[column]
            output.append({
                "county": "Baltimore", "precinct": format_precinct(code), "office": "Governor",
                "district": "", "party": party, "candidate": candidate,
                "votes": str(votes), "winner": won,
            })
    output.sort(key=lambda row: (row["county"], row["precinct"], row["office"], row["district"], row["party"], row["candidate"]))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / "20021105__md__general__precinct.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(output)
    print(f"Wrote {len(output):,} rows to {path}; {len(rows)} Baltimore County precinct/canvass units; totals {totals}")


if __name__ == "__main__":
    main()

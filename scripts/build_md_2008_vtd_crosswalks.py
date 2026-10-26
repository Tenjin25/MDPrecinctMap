"""Build 2008 VTD-to-modern-district crosswalks through Census blocks.

Chain:
  2008 TIGER VTD polygons -> 2000 blocks (representative point)
  -> NHGIS 2000-to-2010 block relationship
  -> NHGIS 2010-to-2020 block relationship
  -> official Maryland 2020-block BEFs

The initial VTD-to-block allocation uses 2000 block land area because a 2000
block population table is not currently stored in this project.  Outputs are
therefore explicitly labeled as provisional historical crosswalks.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.strtree import STRtree


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
VTD_DIR = DATA / "2008 vtds"
OUT_DIR = DATA / "crosswalks_2008_vtd"
VENDOR = ROOT / ".vendor" / "pyshp"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

import shapefile  # type: ignore

from build_md_bef_crosswalks import read_bef, reader_from_zip


PLANS = {
    "congressional_2022_enacted": ("congressional", "SB1012-2022-Md-Congress-BEQ.xlsx"),
    "congressional_2021_mcrc": ("congressional", "MCRC-2021-Final-Congressional-BEF.xlsx"),
    "state_house_2022": ("state_house", "MD-2022OFFICIALLYADOPTEDHOUSE.xlsx"),
    "state_senate_2022": ("state_senate", "MD-2022OFFICIALLYADOPTEDSENATE.xlsx"),
}


def csv_from_zip(path: Path) -> csv.DictReader:
    archive = zipfile.ZipFile(path)
    member = next(name for name in archive.namelist() if name.lower().endswith(".csv"))
    raw = archive.open(member)
    text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
    reader = csv.DictReader(text)
    # Retain handles for the duration of iteration.
    reader._md_handles = (archive, raw, text)  # type: ignore[attr-defined]
    return reader


def normalized_relationships(
    path: Path, source_column: str, target_column: str
) -> dict[str, list[tuple[str, float]]]:
    raw_rows: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    reader = csv_from_zip(path)
    for row in reader:
        source = str(row.get(source_column) or "").strip()
        target = str(row.get(target_column) or "").strip()
        if not source or not target:
            continue
        raw_rows[source].append(
            (target, float(row.get("weight") or 0), float(row.get("parea") or 0))
        )

    result: dict[str, list[tuple[str, float]]] = {}
    for source, rows in raw_rows.items():
        weight_sum = sum(weight for _, weight, _ in rows)
        use_area = weight_sum <= 0
        denominator = sum(area for _, _, area in rows) if use_area else weight_sum
        if denominator <= 0:
            continue
        result[source] = [
            (target, (area if use_area else weight) / denominator)
            for target, weight, area in rows
            if (area if use_area else weight) > 0
        ]
    return result


def load_vtds() -> tuple[dict[str, list[dict[str, object]]], int]:
    by_county: dict[str, list[dict[str, object]]] = defaultdict(list)
    count = 0
    for path in sorted(VTD_DIR.glob("tl_2008_24*_vtd00.zip")):
        reader = reader_from_zip(path)
        for shape_record in reader.iterShapeRecords():
            row = shape_record.record.as_dict()
            county = str(row.get("COUNTYFP00") or "").zfill(3)
            vtd_code = str(row.get("VTDST00") or "").strip()
            if not county or not vtd_code:
                continue
            by_county[county].append(
                {
                    "countyfp00": county,
                    "vtd_code": vtd_code,
                    "vtd_name": str(row.get("NAME00") or "").strip(),
                    "geometry": shape(shape_record.shape.__geo_interface__),
                }
            )
            count += 1
        reader.close()
    return by_county, count


def assign_2000_blocks_to_vtds(
    vtds_by_county: dict[str, list[dict[str, object]]]
) -> tuple[dict[str, tuple[str, str, str, int]], dict[str, int]]:
    trees: dict[str, tuple[STRtree, list[dict[str, object]]]] = {}
    for county, rows in vtds_by_county.items():
        geometries = [row["geometry"] for row in rows]
        trees[county] = (STRtree(geometries), rows)

    assignments: dict[str, tuple[str, str, str, int]] = {}
    diagnostics = {"blocks": 0, "assigned": 0, "nearest_fallback": 0, "missing_county": 0}
    reader = reader_from_zip(DATA / "tl_2010_24_tabblock00.zip")
    for shape_record in reader.iterShapeRecords():
        row = shape_record.record.as_dict()
        block = str(row.get("BLKIDFP00") or "").strip()
        county = str(row.get("COUNTYFP00") or "").zfill(3)
        diagnostics["blocks"] += 1
        if county not in trees:
            diagnostics["missing_county"] += 1
            continue
        point = Point(float(row["INTPTLON00"]), float(row["INTPTLAT00"]))
        tree, vtd_rows = trees[county]
        indices = list(tree.query(point, predicate="intersects"))
        if indices:
            index = int(indices[0])
        else:
            index = int(tree.nearest(point))
            diagnostics["nearest_fallback"] += 1
        vtd = vtd_rows[index]
        assignments[block] = (
            county,
            str(vtd["vtd_code"]),
            str(vtd["vtd_name"]),
            int(row.get("ALAND00") or 0),
        )
        diagnostics["assigned"] += 1
    reader.close()
    return assignments, diagnostics


def block_to_plan_weights(
    block00_to_10: dict[str, list[tuple[str, float]]],
    block10_to_20: dict[str, list[tuple[str, float]]],
    district_by_block20: dict[str, str],
) -> dict[str, dict[str, float]]:
    block10_district: dict[str, dict[str, float]] = {}
    for block10, targets in block10_to_20.items():
        weights: dict[str, float] = defaultdict(float)
        for block20, weight in targets:
            district = district_by_block20.get(block20)
            if district:
                weights[district] += weight
        total = sum(weights.values())
        if total > 0:
            block10_district[block10] = {district: value / total for district, value in weights.items()}

    block00_district: dict[str, dict[str, float]] = {}
    for block00, targets in block00_to_10.items():
        weights: dict[str, float] = defaultdict(float)
        for block10, weight00_10 in targets:
            for district, weight10_district in block10_district.get(block10, {}).items():
                weights[district] += weight00_10 * weight10_district
        total = sum(weights.values())
        if total > 0:
            block00_district[block00] = {district: value / total for district, value in weights.items()}
    return block00_district


def aggregate_vtd_weights(
    assignments: dict[str, tuple[str, str, str, int]],
    block_district: dict[str, dict[str, float]],
) -> list[dict[str, object]]:
    pair_area: dict[tuple[str, str, str, str], float] = defaultdict(float)
    vtd_area: dict[tuple[str, str], float] = defaultdict(float)
    vtd_names: dict[tuple[str, str], str] = {}
    for block, (county, vtd_code, vtd_name, land_area) in assignments.items():
        district_weights = block_district.get(block)
        if not district_weights:
            continue
        basis = float(land_area if land_area > 0 else 1)
        vtd_area[(county, vtd_code)] += basis
        vtd_names[(county, vtd_code)] = vtd_name
        for district, weight in district_weights.items():
            pair_area[(county, vtd_code, vtd_name, district)] += basis * weight

    rows: list[dict[str, object]] = []
    for (county, vtd_code, vtd_name, district), weighted_area in sorted(pair_area.items()):
        total = vtd_area[(county, vtd_code)]
        if total <= 0 or weighted_area <= 0:
            continue
        rows.append(
            {
                "countyfp00": county,
                "vtd_code": vtd_code,
                "vtd_name": vtd_name,
                "district_num": district,
                "allocation_weight": f"{weighted_area / total:.12f}",
                "allocation_basis": "2000_block_land_area_then_nhgis_population_relationships",
            }
        )
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    vtds_by_county, vtd_count = load_vtds()
    assignments, assignment_diagnostics = assign_2000_blocks_to_vtds(vtds_by_county)
    block00_to_10 = normalized_relationships(
        DATA / "nhgis_blk2000_blk2010_24.zip", "blk2000ge", "blk2010ge"
    )
    block10_to_20 = normalized_relationships(
        DATA / "nhgis_blk2010_blk2020_24.zip", "blk2010ge", "blk2020ge"
    )

    outputs = []
    for plan_name, (scope, bef_name) in PLANS.items():
        district_by_block20 = read_bef(DATA / bef_name, scope)
        block_district = block_to_plan_weights(block00_to_10, block10_to_20, district_by_block20)
        rows = aggregate_vtd_weights(assignments, block_district)
        output = OUT_DIR / f"vtd2008_to_{plan_name}.csv"
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        weight_sums: dict[tuple[str, str], float] = defaultdict(float)
        for row in rows:
            weight_sums[(str(row["countyfp00"]), str(row["vtd_code"]))] += float(row["allocation_weight"])
        bad_sums = sum(abs(total - 1) > 1e-8 for total in weight_sums.values())
        outputs.append(
            {
                "plan": plan_name,
                "file": output.name,
                "rows": len(rows),
                "vtds": len(weight_sums),
                "bad_weight_sums": bad_sums,
            }
        )
        print(f"wrote {output.relative_to(ROOT)} ({len(rows)} rows; {len(weight_sums)} VTDs)")

    diagnostics = {
        "status": "provisional",
        "vtd_files": len(list(VTD_DIR.glob("tl_2008_24*_vtd00.zip"))),
        "vtd_features": vtd_count,
        "block_assignment": assignment_diagnostics,
        "method": {
            "vtd_to_block2000": "block internal point; nearest same-county VTD fallback",
            "vtd_block_weight": "2000 Census block land area",
            "block2000_to_2010": "NHGIS normalized population/housing weight; area fallback",
            "block2010_to_2020": "NHGIS normalized population/housing weight; area fallback",
            "block2020_to_district": "official Maryland BEF",
        },
        "outputs": outputs,
    }
    (OUT_DIR / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {(OUT_DIR / 'diagnostics.json').relative_to(ROOT)}")


if __name__ == "__main__":
    main()

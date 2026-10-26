"""Build 2012 VTD-to-modern-district crosswalks through 2010/2020 blocks."""

from __future__ import annotations

import csv
import json
from collections import defaultdict

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from build_md_2008_vtd_crosswalks import normalized_relationships
from build_md_bef_crosswalks import DATA, ROOT, read_bef, reader_from_zip


OUT_DIR = DATA / "crosswalks_2012_vtd"
PLANS = {
    "congressional_2022_enacted": ("congressional", "SB1012-2022-Md-Congress-BEQ.xlsx"),
    "congressional_2021_mcrc": ("congressional", "MCRC-2021-Final-Congressional-BEF.xlsx"),
    "state_house_2022": ("state_house", "MD-2022OFFICIALLYADOPTEDHOUSE.xlsx"),
    "state_senate_2022": ("state_senate", "MD-2022OFFICIALLYADOPTEDSENATE.xlsx"),
}


def load_vtds() -> tuple[dict[str, list[dict[str, object]]], int]:
    by_county: dict[str, list[dict[str, object]]] = defaultdict(list)
    reader = reader_from_zip(DATA / "tl_2012_24_vtd10.zip")
    count = 0
    for shape_record in reader.iterShapeRecords():
        row = shape_record.record.as_dict()
        county = str(row.get("COUNTYFP10") or "").zfill(3)
        code = str(row.get("VTDST10") or "").strip()
        if not county or not code:
            continue
        by_county[county].append(
            {
                "code": code,
                "name": str(row.get("NAME10") or "").strip(),
                "geometry": shape(shape_record.shape.__geo_interface__),
            }
        )
        count += 1
    reader.close()
    return by_county, count


def assign_blocks(vtds_by_county: dict[str, list[dict[str, object]]]):
    trees = {
        county: (STRtree([row["geometry"] for row in rows]), rows)
        for county, rows in vtds_by_county.items()
    }
    assignments = {}
    diagnostics = {"blocks": 0, "assigned": 0, "nearest_fallback": 0, "missing_county": 0}
    reader = reader_from_zip(DATA / "tl_2012_24_tabblock10.zip")
    for shape_record in reader.iterShapeRecords():
        row = shape_record.record.as_dict()
        block = str(row.get("GEOID") or "").strip()
        county = str(row.get("COUNTYFP10") or row.get("COUNTYFP") or "").zfill(3)
        diagnostics["blocks"] += 1
        if county not in trees:
            diagnostics["missing_county"] += 1
            continue
        point = Point(float(row["INTPTLON"]), float(row["INTPTLAT"]))
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
            str(vtd["code"]),
            str(vtd["name"]),
            int(row.get("ALAND") or 0),
        )
        diagnostics["assigned"] += 1
    reader.close()
    return assignments, diagnostics


def block_to_district_weights(block10_to_20, district_by_block20):
    result = {}
    for block10, targets in block10_to_20.items():
        weights = defaultdict(float)
        for block20, weight in targets:
            district = district_by_block20.get(block20)
            if district:
                weights[district] += weight
        total = sum(weights.values())
        if total:
            result[block10] = {district: value / total for district, value in weights.items()}
    return result


def aggregate(assignments, block_district):
    pairs = defaultdict(float)
    totals = defaultdict(float)
    for block, (county, code, name, land_area) in assignments.items():
        weights = block_district.get(block)
        if not weights:
            continue
        basis = float(land_area if land_area > 0 else 1)
        totals[(county, code)] += basis
        for district, weight in weights.items():
            pairs[(county, code, name, district)] += basis * weight
    rows = []
    for (county, code, name, district), value in sorted(pairs.items()):
        rows.append(
            {
                "countyfp10": county,
                "vtd_code": code,
                "vtd_name": name,
                "district_num": district,
                "allocation_weight": f"{value / totals[(county, code)]:.12f}",
                "allocation_basis": "2010_block_land_area_then_nhgis_population_relationship",
            }
        )
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    vtds, vtd_count = load_vtds()
    assignments, assignment_diagnostics = assign_blocks(vtds)
    relationships = normalized_relationships(
        DATA / "nhgis_blk2010_blk2020_24.zip", "blk2010ge", "blk2020ge"
    )
    outputs = []
    for plan, (scope, bef) in PLANS.items():
        block_weights = block_to_district_weights(relationships, read_bef(DATA / bef, scope))
        rows = aggregate(assignments, block_weights)
        path = OUT_DIR / f"vtd2012_to_{plan}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        sums = defaultdict(float)
        for row in rows:
            sums[(row["countyfp10"], row["vtd_code"])] += float(row["allocation_weight"])
        bad = sum(abs(value - 1) > 1e-8 for value in sums.values())
        outputs.append({"plan": plan, "file": path.name, "rows": len(rows), "vtds": len(sums), "bad_weight_sums": bad})
        print(f"wrote {path.relative_to(ROOT)} ({len(rows)} rows; {len(sums)} VTDs)")
    diagnostics = {
        "status": "provisional",
        "vtd_vintage": 2012,
        "vtd_features": vtd_count,
        "block_assignment": assignment_diagnostics,
        "method": {
            "vtd_to_block2010": "block internal point; nearest same-county VTD fallback",
            "vtd_block_weight": "2010 Census block land area",
            "block2010_to_2020": "NHGIS normalized population/housing weight; area fallback",
            "block2020_to_district": "official Maryland BEF",
        },
        "outputs": outputs,
    }
    (OUT_DIR / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

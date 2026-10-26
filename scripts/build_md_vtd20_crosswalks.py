"""Build block-weighted Maryland precinct/VTD crosswalks to 2020 VTDs.

VTD20 is the canonical display geography.  VTD00 and VTD10 are carried
forward through the NHGIS block relationship files; the 2024 precinct layer
is related directly with 2020 Census block internal points.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import transform as transform_geometry
from shapely.strtree import STRtree
from pyproj import Transformer

from build_md_2008_vtd_crosswalks import normalized_relationships
from build_md_bef_crosswalks import DATA, ROOT, reader_from_zip
from build_md_precinct_assets import FIPS_COUNTY, canonical_code


OUT_DIR = DATA / "crosswalks_vtd20"
UTM18_TO_WGS84 = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)


def precinct_key(county_fips: str, code: str) -> str:
    return f"{FIPS_COUNTY.get(county_fips, county_fips)} - {canonical_code(code)}".upper()


def load_vtd_polygons(path: Path, county_field: str, code_field: str):
    by_county = defaultdict(list)
    reader = reader_from_zip(path)
    for shape_record in reader.iterShapeRecords():
        row = shape_record.record.as_dict()
        county = str(row.get(county_field) or "").zfill(3)
        code = canonical_code(row.get(code_field))
        if not county or not code:
            continue
        by_county[county].append(
            {"key": precinct_key(county, code), "geometry": shape(shape_record.shape.__geo_interface__)}
        )
    reader.close()
    return by_county


def load_vtd00_polygons():
    by_county = defaultdict(list)
    for path in sorted((DATA / "2008 vtds").glob("tl_2008_24*_vtd00.zip")):
        layer = load_vtd_polygons(path, "COUNTYFP00", "VTDST00")
        for county, rows in layer.items():
            by_county[county].extend(rows)
    return by_county


def load_current_polygons():
    by_county = defaultdict(list)
    reader = reader_from_zip(DATA / "md_2024_gen_prec.zip")
    for shape_record in reader.iterShapeRecords():
        row = shape_record.record.as_dict()
        county = str(row.get("COUNTYFP") or "").zfill(3)
        district_raw = str(row.get("Elec_Dist") or "")
        precinct_raw = str(row.get("Precinct") or "")
        if "Z" in district_raw.upper() or "Z" in precinct_raw.upper():
            continue
        code = canonical_code(f"{district_raw}-{precinct_raw}")
        if not county or not code:
            continue
        by_county[county].append(
            {
                "key": precinct_key(county, code),
                "geometry": transform_geometry(
                    UTM18_TO_WGS84.transform, shape(shape_record.shape.__geo_interface__)
                ),
            }
        )
    reader.close()
    return by_county


def assign_blocks(
    polygons_by_county,
    block_path: Path,
    block_field: str,
    county_field: str,
    lon_field: str,
    lat_field: str,
    area_field: str,
):
    trees = {
        county: (STRtree([row["geometry"] for row in rows]), rows)
        for county, rows in polygons_by_county.items()
    }
    assignments = {}
    diagnostics = {"blocks": 0, "assigned": 0, "nearest_fallback": 0, "missing_county": 0}
    reader = reader_from_zip(block_path)
    for shape_record in reader.iterShapeRecords():
        row = shape_record.record.as_dict()
        block = str(row.get(block_field) or "").strip()
        county = str(row.get(county_field) or "").zfill(3)
        diagnostics["blocks"] += 1
        if not block or county not in trees:
            diagnostics["missing_county"] += 1
            continue
        point = Point(float(row[lon_field]), float(row[lat_field]))
        tree, polygon_rows = trees[county]
        indices = list(tree.query(point, predicate="intersects"))
        if indices:
            index = int(indices[0])
        else:
            index = int(tree.nearest(point))
            diagnostics["nearest_fallback"] += 1
        assignments[block] = (polygon_rows[index]["key"], int(row.get(area_field) or 0))
        diagnostics["assigned"] += 1
    reader.close()
    return assignments, diagnostics


def relationship_to_targets(relationships, next_targets):
    output = {}
    for source_block, related in relationships.items():
        weights = defaultdict(float)
        for next_block, relationship_weight in related:
            for target, target_weight in next_targets.get(next_block, {}).items():
                weights[target] += relationship_weight * target_weight
        total = sum(weights.values())
        if total > 0:
            output[source_block] = {target: value / total for target, value in weights.items()}
    return output


def direct_targets(assignments):
    return {block: {target: 1.0} for block, (target, _area) in assignments.items()}


def aggregate(source_assignments, block_targets):
    pair_weights = defaultdict(float)
    source_totals = defaultdict(float)
    for block, (source, land_area) in source_assignments.items():
        targets = block_targets.get(block)
        if not targets:
            continue
        basis = float(land_area if land_area > 0 else 1)
        source_totals[source] += basis
        for target, weight in targets.items():
            pair_weights[(source, target)] += basis * weight

    rows = []
    for (source, target), value in sorted(pair_weights.items()):
        total = source_totals[source]
        if value <= 0 or total <= 0:
            continue
        rows.append({"source": source, "target": target, "weight": value / total})
    return rows


def write_csv(name: str, rows, basis: str):
    path = OUT_DIR / name
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source", "target", "allocation_weight", "allocation_basis"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "source": row["source"],
                    "target": row["target"],
                    "allocation_weight": f"{row['weight']:.12f}",
                    "allocation_basis": basis,
                }
            )
    return path


def json_crosswalk(rows, method: str):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["source"]].append([row["target"], round(row["weight"], 12)])
    return {
        "method": method,
        "splits": [
            {"source": source, "targets": targets, "method": method}
            for source, targets in sorted(grouped.items())
        ],
        "unresolved_current": [],
    }


def summarize(rows):
    totals = defaultdict(float)
    targets = defaultdict(int)
    for row in rows:
        totals[row["source"]] += row["weight"]
        targets[row["source"]] += 1
    return {
        "rows": len(rows),
        "source_precincts": len(totals),
        "split_sources": sum(count > 1 for count in targets.values()),
        "bad_weight_sums": sum(abs(total - 1.0) > 1e-8 for total in totals.values()),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    vtd20_polygons = load_vtd_polygons(DATA / "tl_2020_24_vtd20.zip", "COUNTYFP20", "VTDST20")
    block20_assignments, target_diag = assign_blocks(
        vtd20_polygons, DATA / "tl_2020_24_tabblock20.zip", "GEOID20", "COUNTYFP20",
        "INTPTLON20", "INTPTLAT20", "ALAND20"
    )
    block20_targets = direct_targets(block20_assignments)

    vtd10_polygons = load_vtd_polygons(DATA / "tl_2012_24_vtd10.zip", "COUNTYFP10", "VTDST10")
    block10_assignments, vtd10_diag = assign_blocks(
        vtd10_polygons, DATA / "tl_2012_24_tabblock10.zip", "GEOID", "COUNTYFP10",
        "INTPTLON", "INTPTLAT", "ALAND"
    )
    rel10_20 = normalized_relationships(DATA / "nhgis_blk2010_blk2020_24.zip", "blk2010ge", "blk2020ge")
    block10_targets = relationship_to_targets(rel10_20, block20_targets)
    vtd10_rows = aggregate(block10_assignments, block10_targets)

    vtd00_polygons = load_vtd00_polygons()
    block00_assignments, vtd00_diag = assign_blocks(
        vtd00_polygons, DATA / "tl_2010_24_tabblock00.zip", "BLKIDFP00", "COUNTYFP00",
        "INTPTLON00", "INTPTLAT00", "ALAND00"
    )
    rel00_10 = normalized_relationships(DATA / "nhgis_blk2000_blk2010_24.zip", "blk2000ge", "blk2010ge")
    block00_targets = relationship_to_targets(rel00_10, block10_targets)
    vtd00_rows = aggregate(block00_assignments, block00_targets)

    current_polygons = load_current_polygons()
    current_assignments, current_diag = assign_blocks(
        current_polygons, DATA / "tl_2020_24_tabblock20.zip", "GEOID20", "COUNTYFP20",
        "INTPTLON20", "INTPTLAT20", "ALAND20"
    )
    current_rows = aggregate(current_assignments, block20_targets)

    files = {
        "vtd00": write_csv("vtd00_to_vtd20.csv", vtd00_rows, "2000 block land area through NHGIS 2000-2010-2020 relationships"),
        "vtd10": write_csv("vtd10_to_vtd20.csv", vtd10_rows, "2010 block land area through NHGIS 2010-2020 relationship"),
        "current2024": write_csv("precinct2024_to_vtd20.csv", current_rows, "2020 block land area by internal-point assignment"),
    }

    crosswalks = {
        "vtd00": json_crosswalk(vtd00_rows, "block00_nhgis_block10_nhgis_block20_to_vtd20"),
        "vtd10": json_crosswalk(vtd10_rows, "block10_nhgis_block20_to_vtd20"),
        "current2024": json_crosswalk(current_rows, "block20_internal_point_to_vtd20"),
    }
    payload = {
        "default_geometry": "vtd20",
        "crosswalks": crosswalks,
        "years": {
            **{str(year): crosswalks["vtd00"] for year in (2000, 2002, 2004, 2006, 2008)},
            **{str(year): crosswalks["vtd10"] for year in (2010, 2012, 2014, 2016, 2018)},
            **{str(year): crosswalks["current2024"] for year in (2023, 2024)},
        },
    }
    json_path = DATA / "precinct_result_geometry_crosswalk.json"
    json_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    diagnostics = {
        "status": "provisional",
        "canonical_geometry": "2020 Census VTD (VTD20)",
        "block_assignment": {"vtd20": target_diag, "vtd10": vtd10_diag, "vtd00": vtd00_diag, "current2024": current_diag},
        "outputs": {
            key: {"file": str(path.relative_to(ROOT)), **summarize(rows)}
            for (key, path), rows in zip(files.items(), (vtd00_rows, vtd10_rows, current_rows))
        },
        "notes": [
            "Weights use Census block land area because block population counts are not stored with these TIGER shapefiles.",
            "NHGIS population/housing relationship weights are used between Census block vintages, with area fallback.",
        ],
    }
    (OUT_DIR / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8")
    for key, path in files.items():
        print(f"wrote {path.relative_to(ROOT)}: {summarize({'vtd00': vtd00_rows, 'vtd10': vtd10_rows, 'current2024': current_rows}[key])}")
    print(f"wrote {json_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

"""Build standardized Maryland VTD/precinct GeoJSON and name indexes."""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.ops import transform as transform_geometry
from pyproj import Transformer

from build_md_bef_crosswalks import DATA, ROOT, reader_from_zip
from build_md_2008_sbe_president_proxy import COUNTY_FIPS


FIPS_COUNTY = {fips: name.title() for name, fips in COUNTY_FIPS.items()}
FIPS_COUNTY.update(
    {
        "003": "Anne Arundel",
        "005": "Baltimore",
        "033": "Prince George's",
        "035": "Queen Anne's",
        "037": "St. Mary's",
        "510": "Baltimore City",
    }
)
UTM18_TO_WGS84 = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)


def canonical_code(raw: object) -> str:
    text = str(raw or "").strip().upper()
    if not text or text == "ZZZZZZ":
        return ""
    parts = re.findall(r"\d+", text)
    if len(parts) >= 2:
        return f"{int(parts[0]):03d}-{int(parts[1]):03d}"
    return text


def rounded(value, digits: int = 6):
    if isinstance(value, (list, tuple)):
        if value and isinstance(value[0], (int, float)):
            return [round(float(item), digits) for item in value]
        return [rounded(item, digits) for item in value]
    return value


def standardized_properties(county_fips: str, code: str, full_name: str, source: str, source_id: str):
    county = FIPS_COUNTY.get(county_fips, county_fips)
    norm = f"{county} - {code}".upper()
    return {
        "COUNTYFP": county_fips,
        "county_nam": county,
        "county_norm": county.upper(),
        "prec_id": code,
        "precinct_name": f"{county} - {code}",
        "precinct_norm": norm,
        "precinct_full_name": full_name,
        "source_vintage": source,
        "source_id": source_id,
    }


def build_vtd_zip(path: Path, vintage: str, county_field: str, code_field: str, name_field: str):
    reader = reader_from_zip(path)
    features = []
    centroids = []
    for shape_record in reader.iterShapeRecords():
        row = shape_record.record.as_dict()
        code = canonical_code(row.get(code_field))
        if not code:
            continue
        county_fips = str(row.get(county_field) or "").zfill(3)
        county = FIPS_COUNTY.get(county_fips, county_fips)
        original_name = str(row.get(name_field) or "").strip()
        full_name = original_name or f"{county} Precinct {code}"
        props = standardized_properties(county_fips, code, full_name, vintage, str(row.get("GEOID20") or row.get("GEOID10") or row.get("VTDIDFP00") or ""))
        geom = dict(shape_record.shape.__geo_interface__)
        geom["coordinates"] = rounded(geom["coordinates"])
        features.append({"type": "Feature", "properties": props, "geometry": geom})
        point = shape(geom).representative_point()
        centroids.append({"type": "Feature", "properties": dict(props), "geometry": mapping(point)})
    reader.close()
    return features, centroids


def build_vtd00():
    features = []
    centroids = []
    for path in sorted((DATA / "2008 vtds").glob("tl_2008_24*_vtd00.zip")):
        f, c = build_vtd_zip(path, "2008 VTD00", "COUNTYFP00", "VTDST00", "NAME00")
        features.extend(f)
        centroids.extend(c)
    return features, centroids


def build_current_2024():
    reader = reader_from_zip(DATA / "md_2024_gen_prec.zip")
    features = []
    centroids = []
    for shape_record in reader.iterShapeRecords():
        row = shape_record.record.as_dict()
        county_fips = str(row.get("COUNTYFP") or "").zfill(3)
        county = str(row.get("County") or FIPS_COUNTY.get(county_fips, county_fips)).strip()
        FIPS_COUNTY[county_fips] = county
        district_raw = str(row.get("Elec_Dist") or "")
        precinct_raw = str(row.get("Precinct") or "")
        if "Z" in district_raw.upper() or "Z" in precinct_raw.upper():
            continue
        code = canonical_code(f"{district_raw}-{precinct_raw}")
        if not code:
            continue
        props = standardized_properties(county_fips, code, f"{county} Precinct {code}", "2024 RDH/SBE precinct", str(row.get("UNIQUE_ID") or ""))
        source_geom = shape(shape_record.shape.__geo_interface__)
        projected_geom = transform_geometry(UTM18_TO_WGS84.transform, source_geom)
        geom = mapping(projected_geom)
        geom["coordinates"] = rounded(geom["coordinates"])
        features.append({"type": "Feature", "properties": props, "geometry": geom})
        point = shape(geom).representative_point()
        centroids.append({"type": "Feature", "properties": dict(props), "geometry": mapping(point)})
    reader.close()
    return features, centroids


def write_geojson(name: str, features):
    path = DATA / name
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)} ({len(features)} features)")


def build_name_indexes(feature_groups):
    friendly = defaultdict(dict)
    aliases = defaultdict(lambda: defaultdict(set))

    reference = DATA / "State_Precinct_Reference_2010_Primary.csv"
    with reference.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            county = str(row.get("County Name") or "").strip()
            code = f"{int(row.get('Election District') or 0):03d}-{int(row.get('Election Precinct') or 0):03d}"
            polling_place = str(row.get("Polling Place") or "").strip()
            if county and code and polling_place:
                friendly[county][code] = polling_place

    for features in feature_groups:
        for feature in features:
            props = feature["properties"]
            county = str(props["county_nam"])
            code = str(props["prec_id"])
            raw_code = canonical_code(code)
            district, precinct = (int(part) for part in raw_code.split("-", 1))
            variants = {
                code,
                f"{district}-{precinct}",
                f"{district:02d}-{precinct:03d}",
                f"{district:03d}-{precinct:03d}",
                str(props.get("precinct_full_name") or "").upper(),
                str(props.get("precinct_name") or "").upper(),
            }
            for alias in variants:
                if alias:
                    aliases[county][alias].add(code)
            if code not in friendly[county]:
                friendly[county][code] = f"Precinct {code}"

    friendly_payload = {"counties": {county: dict(sorted(codes.items())) for county, codes in sorted(friendly.items())}}
    alias_payload = {
        "counties": {
            county: {alias: sorted(codes) for alias, codes in sorted(alias_map.items())}
            for county, alias_map in sorted(aliases.items())
        }
    }
    (DATA / "precinct_friendly_names.json").write_text(json.dumps(friendly_payload, indent=2) + "\n", encoding="utf-8")
    (DATA / "precinct_alias_index.json").write_text(json.dumps(alias_payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote data/precinct_friendly_names.json ({sum(len(v) for v in friendly.values())} labels)")
    print(f"wrote data/precinct_alias_index.json ({sum(len(v) for v in aliases.values())} aliases)")


def main() -> None:
    vtd00, _ = build_vtd00()
    vtd10, _ = build_vtd_zip(DATA / "tl_2012_24_vtd10.zip", "2012 VTD10", "COUNTYFP10", "VTDST10", "NAME10")
    vtd20, vtd20_centroids = build_vtd_zip(DATA / "tl_2020_24_vtd20.zip", "2020 VTD20", "COUNTYFP20", "VTDST20", "NAME20")
    current, _ = build_current_2024()
    write_geojson("md_vtd_2008.geojson", vtd00)
    write_geojson("md_vtd_2012.geojson", vtd10)
    write_geojson("md_vtd_2020.geojson", vtd20)
    write_geojson("md_precincts_2024.geojson", current)
    write_geojson("md_vtd_2020_centroids.geojson", vtd20_centroids)
    build_name_indexes([vtd00, vtd10, vtd20, current])


if __name__ == "__main__":
    main()

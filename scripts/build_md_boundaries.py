"""Build the Maryland boundary GeoJSON files used by the atlas.

Inputs are official Maryland and Census shapefile ZIPs stored in ``data``.
The script deliberately keeps only the attributes consumed by ``index.html``.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
VENDOR = ROOT / ".vendor" / "pyshp"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

import shapefile  # type: ignore  # provided in .vendor/pyshp


def reader_from_zip(path: Path) -> shapefile.Reader:
    with zipfile.ZipFile(path) as archive:
        stem = next(name[:-4] for name in archive.namelist() if name.lower().endswith(".shp"))
        return shapefile.Reader(
            shp=io.BytesIO(archive.read(f"{stem}.shp")),
            shx=io.BytesIO(archive.read(f"{stem}.shx")),
            dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
        )


def rounded_coordinates(value, digits: int = 6):
    if isinstance(value, (list, tuple)):
        if value and isinstance(value[0], (int, float)):
            return [round(float(item), digits) for item in value]
        return [rounded_coordinates(item, digits) for item in value]
    return value


def write_geojson(path: Path, features: list[dict]) -> None:
    collection = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(collection, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)} ({len(features)} features)")


def build_counties(source: Path) -> None:
    reader = reader_from_zip(source)
    features: list[dict] = []
    for shape_record in reader.iterShapeRecords():
        record = shape_record.record.as_dict()
        state_fips = str(record.get("STATEFP") or record.get("STATEFP20") or "")
        if state_fips and state_fips != "24":
            continue
        geometry = dict(shape_record.shape.__geo_interface__)
        geometry["coordinates"] = rounded_coordinates(geometry["coordinates"])
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "STATEFP": state_fips or "24",
                    "COUNTYFP": record.get("COUNTYFP") or record.get("COUNTYFP20"),
                    "GEOID": record.get("GEOID") or record.get("GEOID20"),
                    "NAME": record.get("NAME") or record.get("NAME20"),
                    "NAMELSAD": record.get("NAMELSAD") or record.get("NAMELSAD20"),
                },
                "geometry": geometry,
            }
        )
    write_geojson(DATA / "md_counties.geojson", features)


def build_legislative(source: Path, output: Path, chamber: str) -> None:
    reader = reader_from_zip(source)
    features: list[dict] = []
    for shape_record in reader.iterShapeRecords():
        record = shape_record.record.as_dict()
        raw = str(record.get("DISTRICT") or record.get("DISTRICT_N") or "").strip().upper()
        district = raw.removeprefix("SD").removeprefix("HD")
        geometry = dict(shape_record.shape.__geo_interface__)
        geometry["coordinates"] = rounded_coordinates(geometry["coordinates"])
        properties = {
            "DISTRICT": district,
            "DISTRICT_N": district,
            "NAMELSAD": f"{chamber} District {district}",
            "MEMBERS": record.get("MEMBERS"),
            "ADJ_POPULA": record.get("ADJ_POPULA"),
            "ADJ_18_POP": record.get("ADJ_18_POP"),
            "F_ADJ_HISP": record.get("F_ADJ_HISP"),
            "F_ADJ_NH_W": record.get("F_ADJ_NH_W"),
            "F_ADJ_NH_B": record.get("F_ADJ_NH_B"),
        }
        features.append({"type": "Feature", "properties": properties, "geometry": geometry})
    features.sort(key=lambda feature: int(str(feature["properties"]["DISTRICT"]).rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")))
    write_geojson(output, features)


def build_congressional(source: Path, output: Path, plan: str) -> None:
    reader = reader_from_zip(source)
    features: list[dict] = []
    for shape_record in reader.iterShapeRecords():
        record = shape_record.record.as_dict()
        district = str(record.get("CD119FP") or record.get("DISTRICT") or "").strip()
        district_number = int(district) if district.isdigit() else district
        geometry = dict(shape_record.shape.__geo_interface__)
        geometry["coordinates"] = rounded_coordinates(geometry["coordinates"])
        properties = {
            "DISTRICT": district_number,
            "CD119FP": str(district_number).zfill(2) if isinstance(district_number, int) else district,
            "NAMELSAD": f"Congressional District {district_number}",
            "PLAN": plan,
        }
        for key in ("ADJ_POPULA", "ADJ_18_POP", "F_ADJ_HISP", "F_ADJ_NH_W", "F_ADJ_NH_B"):
            if record.get(key) is not None:
                properties[key] = record[key]
        features.append({"type": "Feature", "properties": properties, "geometry": geometry})
    features.sort(key=lambda feature: int(feature["properties"]["DISTRICT"]))
    write_geojson(output, features)


def main() -> None:
    county_source = DATA / "tl_2024_us_county.zip"
    if not county_source.exists():
        county_source = DATA / "tl_2020_24_county20.zip"
    build_counties(county_source)
    build_congressional(
        DATA / "tl_2024_24_cd119.zip",
        DATA / "md_congress_enacted.geojson",
        "2022 enacted congressional plan (119th Congress TIGER/Line representation)",
    )
    build_congressional(
        DATA / "MCRC-2021-Final-Congressional-Shapefile.zip",
        DATA / "md_congress_hogan_mcrc.geojson",
        "2021 Maryland Citizens Redistricting Commission final recommendation",
    )
    build_legislative(
        DATA / "2022HOUSEOFFICIAL.zip",
        DATA / "md_state_house_enacted.geojson",
        "House of Delegates",
    )
    build_legislative(
        DATA / "2022SENATEOFFICIAL.zip",
        DATA / "md_state_senate_enacted.geojson",
        "State Senate",
    )


if __name__ == "__main__":
    main()

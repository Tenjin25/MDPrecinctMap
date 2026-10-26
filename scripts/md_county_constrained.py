"""Shared county-constrained district vote allocation helpers.

Each county/party total is allocated independently across its districts with
largest-remainder rounding.  A county wholly contained in one district thus
contributes its exact canonical total, while split counties retain the spatial
distribution supplied by block, VTD, or precinct weights.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping


PARTIES = ("dem", "rep", "other")


def largest_remainder(values: Mapping[str, float], target: int) -> dict[str, int]:
    """Allocate an integer target proportionally while preserving its exact sum."""
    clean = {str(key): max(0.0, float(value)) for key, value in values.items()}
    if target < 0:
        raise ValueError(f"Negative allocation target: {target}")
    if not clean:
        if target:
            raise ValueError(f"Cannot allocate {target} votes without district weights")
        return {}
    raw_total = sum(clean.values())
    if raw_total <= 0:
        if target:
            raise ValueError(f"Cannot allocate {target} votes from zero-valued district weights")
        return {district: 0 for district in clean}

    scaled = {district: target * value / raw_total for district, value in clean.items()}
    output = {district: math.floor(value) for district, value in scaled.items()}
    remainder = target - sum(output.values())
    order = sorted(
        scaled,
        key=lambda district: (scaled[district] - output[district], district),
        reverse=True,
    )
    for district in order[:remainder]:
        output[district] += 1
    return output


def apportion_counties_to_districts(
    county_district_totals: Mapping[str, Mapping[str, Mapping[str, float]]],
    county_targets: Mapping[str, Mapping[str, int]] | None = None,
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, dict[str, int]]]]:
    """Round within each county/party, then sum county contributions by district.

    ``county_targets`` may supply canonical integer county totals.  When it is
    omitted, the rounded sum of the input rows is used as the county target.
    """
    district_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {party: 0 for party in PARTIES}
    )
    contributions: dict[str, dict[str, dict[str, int]]] = {}

    counties = set(county_district_totals)
    if county_targets is not None:
        counties |= set(county_targets)

    for county in sorted(counties):
        district_rows = county_district_totals.get(county, {})
        target_row = county_targets.get(county, {}) if county_targets is not None else {}
        county_output: dict[str, dict[str, int]] = {
            str(district): {party: 0 for party in PARTIES}
            for district in district_rows
        }
        for party in PARTIES:
            raw = {
                str(district): float(values.get(party, 0))
                for district, values in district_rows.items()
            }
            target = int(target_row.get(party, round(sum(raw.values()))))
            allocated = largest_remainder(raw, target)
            for district, votes in allocated.items():
                county_output[district][party] = votes
                district_totals[district][party] += votes
        contributions[county] = county_output

    return dict(district_totals), contributions


def county_component_catalog(
    county_district_totals: Mapping[str, Mapping[str, Mapping[str, float]]],
) -> dict[str, dict[str, list[str]]]:
    """Describe exact whole-county and allocated split-county components."""
    exact: dict[str, list[str]] = defaultdict(list)
    split: dict[str, list[str]] = defaultdict(list)
    for county, rows in county_district_totals.items():
        # Callers create a row whenever the county geometry touches a district,
        # so even a zero-vote block remains a meaningful plan component.
        districts = [str(district) for district in rows]
        if len(districts) == 1:
            exact[districts[0]].append(str(county))
        elif len(districts) > 1:
            for district in districts:
                split[district].append(str(county))
    return {
        "exact_components": {key: sorted(value) for key, value in sorted(exact.items())},
        "allocated_components": {key: sorted(value) for key, value in sorted(split.items())},
    }
